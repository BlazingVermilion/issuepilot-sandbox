"""CLI for reproducible RAG experiments. Outputs never authorize real actions."""

import argparse, copy, importlib.metadata, json, platform, sys, time
from pathlib import Path
import jsonschema
import retrieval as util
import dataset, contracts, storage, judging, pilot, rag_generation, rag_refined
from client import Ollama, CallError
from progress import Progress
from evaluation import summarize

ROOT = Path(__file__).resolve().parent
import bench_config as cfg
JUDGE_CHALLENGES = cfg.CHALLENGES
MODES = ["bm25", "dense", "hybrid", "hybrid-rerank", "policy", "full", "oracle", "none"]
PILOT_PROTOCOLS = {"pilot": "single-call-policy-v1", "pilot-refined": "single-call-policy-v3"}


def release_cuda_cache():
    import gc
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()


def identity(a, client):
    packages = {}
    for n in (
        "jsonschema",
        "numpy",
        "sentence-transformers",
        "transformers",
        "huggingface-hub",
        "torch",
    ):
        try:
            packages[n] = importlib.metadata.version(n)
        except importlib.metadata.PackageNotFoundError:
            pass
    return {
        "version": "ai-bench-0.2.1",
        "protocol": PILOT_PROTOCOLS.get(getattr(a, "strategy", None), "decision-payload-v2"),
        "thinking": client.meta.get("thinking_control", False),
        "dependencies": dataset.DEPS,
        "code_hash": util.sha(
            b"".join((p.read_bytes() for p in sorted(ROOT.glob("*.py"))))
        ),
        "policy_hash": util.sha(dataset.POLICY.read_bytes()),
        "data_hash": util.sha(dataset.DATA.read_bytes()),
        "model_digest": client.meta["digest"],
        "model": a.model,
        "seed": a.seed,
        "num_ctx": a.num_ctx,
        "max_output": a.max_output,
        "budget_tokenizer": client.meta.get("budget_tokenizer"),
        "packages": packages,
        "python": platform.python_version(),
        "ollama_version": client.meta["ollama_version"],
    }


def failure(e):
    kind = (
        e.kind
        if isinstance(e, CallError)
        else (
            "SCHEMA_INVALID"
            if isinstance(e, jsonschema.ValidationError)
            else "VALIDATION_OR_RUNTIME_ERROR"
        )
    )
    if "ANSWER_WITHOUT_CONTEXT" in str(e):
        kind = "ANSWER_WITHOUT_CONTEXT"
    return {"kind": kind, "detail": str(e)[:700]}


def call(client, system, user, schema, calls, stage, maximum=None):
    try:
        value, meta = client.chat(system, user, schema, maximum)
        calls.append({"stage": stage, "meta": meta})
        return value
    except CallError as e:
        calls.append({"stage": stage, "meta": e.meta, "error": failure(e)})
        raise


def make_retriever(a, mode):
    return util.Retriever(
        dataset.POLICY,
        mode,
        device=a.device,
        qdrant_url=getattr(a, "qdrant_url", "http://localhost:6333"),
        timeout=getattr(a, "timeout", 60),
    )


def precheck(client, ret, cases, a):
    progress = Progress("RAG " + a.mode + " preflight", len(cases), every=10)
    for c in cases:
        progress.start(c["id"])
        docs, _ = dataset.assemble(ret, c, a.mode, a.k)
        if a.strategy in PILOT_PROTOCOLS:
            timing = rag_generation.timing_check(c["query"], docs) if c["task"] == "action" else None
            if docs and (timing is None or timing["met"]):
                prompt = rag_refined.prompt if a.strategy == "pilot-refined" else rag_generation.user_prompt
                client.budget(
                    rag_generation.SYSTEM,
                    prompt(c["query"], c["task"], docs, timing),
                    a.max_output,
                )
            progress.finish("CHECKED")
            continue
        client.budget(
            contracts.DECISION_SYSTEM + contracts.PAYLOAD_SYSTEM,
            contracts.prompt(c["query"], c["task"], docs) + "\nSELECTED DECISION: EXPLANATION",
            a.max_output,
        )
        if a.strategy == "evidence-first":
            client.budget(
                contracts.EXTRACT_SYSTEM,
                contracts.prompt(c["query"], c["task"], docs),
                a.extraction_output,
            )
            bound = client.budget(
                contracts.DECISION_SYSTEM + contracts.PAYLOAD_SYSTEM,
                contracts.prompt(c["query"], c["task"], docs),
                a.max_output,
            )
            if bound + 8000 > a.num_ctx:
                raise CallError(
                    "CONTEXT_BUDGET",
                    "Evidence-first needs a further 8000-token worst-case extraction reserve",
                )

        progress.finish("CHECKED")


def generate_pilot(client, case, docs, record, strategy="pilot"):
    task, query = case["task"], case["query"]
    timing = rag_generation.timing_check(query, docs) if task == "action" else None
    if timing is not None:
        record["computed_timing"] = timing
    if timing is not None and not timing["met"]:
        record["generation_path"] = "deadline_check"
        record["output"] = rag_generation.deterministic_block(timing, docs, task)
        record["first_attempt_valid"] = True
        return
    if not docs:
        record["generation_path"] = "no_context"
        record["output"], _ = rag_generation.normalize_wire(
            {"decision": "NO_POLICY", "claims": []}, docs, task
        )
        record["first_attempt_valid"] = True
        return

    record["generation_path"] = "model"
    record["first_attempt_valid"] = False
    refined = strategy == "pilot-refined"
    prompt = rag_refined.prompt if refined else rag_generation.user_prompt
    schema = rag_refined.wire_schema if refined else rag_generation.wire_schema
    wire_schema = schema(docs, task)
    wire = call(client, rag_generation.SYSTEM,
                prompt(query, task, docs, timing),
                wire_schema, record["calls"], "answer")
    if isinstance(wire, dict):
        record["raw_decision"] = {"decision": wire.get("decision")}
    record["raw_payload"] = wire
    record["raw_citations"] = contracts.raw_citations(wire)
    value, normalizations = rag_generation.normalize_wire(wire, docs, task)
    if refined and value["policy_answerable"]:
        normalized_wire = {"decision": wire["decision"], "claims": value["claims"]}
        jsonschema.validate(normalized_wire, wire_schema)
    if refined and timing is not None and value["policy_answerable"]:
        claim = rag_generation.timing_claim(timing)
        if all(item["text"] != claim for item in value["claims"]):
            value["claims"].insert(0, {"text": claim, "citations": [timing["sources"][0]]})
            record["computed_claim_added"] = True
    contracts.validate_output(value, list(docs), task)
    record["normalizations"] = normalizations
    record["first_attempt_valid"] = not normalizations
    record["output"] = value


def run(a, preflight=False):
    dataset.validate()
    cases = dataset.load(a.split)
    if not cases: raise ValueError("No scenarios in the requested split")
    if a.limit:
        cases = cases[: a.limit]
    client = Ollama(a)
    base = identity(a, client)
    base.update(
        split=a.split,
        scenario_ids=[r["id"] for r in cases],
        k=a.k,
        device=a.device,
        strategy=a.strategy,
        extraction_output=a.extraction_output,
    )
    if not preflight:
        root = Path(a.out)
        with storage.exclusive(root):
            planpath = root / "experiment.json"
            plan = storage.read(planpath) if planpath.exists() else {"planned_arms": []}
            for mode in a.modes:
                name = f"{mode}--{a.strategy}"
                if name not in plan["planned_arms"]:
                    plan["planned_arms"].append(name)
            util.dump(planpath, plan)
    for mode in a.modes:
        ret = make_retriever(a, mode)
        aa = copy.copy(a)
        aa.mode = mode
        precheck(client, ret, cases, aa)
        print("PREFLIGHT", mode, "PASS", flush=True)
        del ret
        release_cuda_cache()
    if preflight:
        return
    for arm_index, mode in enumerate(a.modes, 1):
        folder = Path(a.out) / f"{mode}--{a.strategy}"
        with storage.exclusive(folder):
            ret = make_retriever(a, mode)
            ident = {
                **base,
                "mode": mode,
                "retrieval_revisions": ret.meta.get("model_revisions", {}),
                "vector_db": ret.meta.get("vector_db"),
            }
            m = storage.initialize(folder, ident, a.resume)
            m.update(
                status="RUNNING",
                runtime=client.meta,
                retriever=ret.meta,
                execution_order=a.modes,
            )
            util.dump(folder / "manifest.json", m)
            prior = storage.records(folder, base["scenario_ids"])
            done = {r["id"] for r in prior}
            progress = Progress(f"RAG {mode}/{a.strategy} arm {arm_index}/{len(a.modes)}", len(cases), len(done), sum(bool(r.get("error")) for r in prior))
            try:
                for c in cases:
                    if c["id"] in done:
                        continue
                    progress.start(c["id"])
                    r = {
                        "id": c["id"],
                        "gold": c,
                        "output": None,
                        "error": None,
                        "calls": [],
                        "context_ids": [],
                        "ranked_ids": None,
                    }
                    start = time.perf_counter()
                    stage = "retrieval"
                    try:
                        t = time.perf_counter()
                        docs, rank = dataset.assemble(ret, c, mode, a.k)
                        r.update(
                            context=docs,
                            context_ids=list(docs),
                            ranked_ids=rank,
                            retrieval_seconds=time.perf_counter() - t,
                        )
                        if a.strategy in PILOT_PROTOCOLS:
                            stage = "answer"
                            generate_pilot(client, c, docs, r, a.strategy)
                        else:
                            evidence = None
                            if a.strategy == "evidence-first":
                                stage = "extract"
                                evidence = call(
                                    client,
                                    contracts.EXTRACT_SYSTEM,
                                    contracts.prompt(c["query"], c["task"], docs),
                                    contracts.extraction_schema(list(docs)),
                                    r["calls"],
                                    "extract",
                                    a.extraction_output,
                                )
                                r["extraction"] = evidence
                                contracts.validate_extraction(evidence, docs)
                            stage = "decision"
                            user = contracts.prompt(c["query"], c["task"], docs, evidence)
                            decision = call(client, contracts.DECISION_SYSTEM, user,
                                contracts.decision_schema(c["task"]), r["calls"], "decision")
                            r["raw_decision"] = decision
                            jsonschema.validate(decision, contracts.decision_schema(c["task"]))
                            stage = "payload"
                            wire_schema = contracts.payload_schema(list(docs), decision["decision"])
                            payload = call(client, contracts.PAYLOAD_SYSTEM,
                                user + "\nSELECTED DECISION: " + decision["decision"],
                                wire_schema, r["calls"], "payload")
                            r["raw_payload"] = payload
                            r["raw_citations"] = contracts.raw_citations(payload)
                            v = contracts.normalize(decision["decision"], payload, list(docs), c["task"])
                            r["raw_output"] = v
                            contracts.validate_output(v, list(docs), c["task"])
                            r["output"] = v
                    except Exception as e:
                        r["error"] = {**failure(e), "stage": stage}
                    r["latency_seconds"] = time.perf_counter() - start
                    util.dump(folder / "records" / f"{c['id']}.json", r)
                    progress.finish(r["error"]["kind"] if r["error"] else "VALID_OUTPUT", error=bool(r["error"]))
                rows = storage.records(folder, base["scenario_ids"])
                storage.export(folder, rows)
                util.dump(folder / "summary.json", summarize(rows, len(cases)))
                m["status"] = "COMPLETED"
                m["predictions_hash"] = util.sha(
                    (folder / "predictions.jsonl").read_bytes()
                )
                util.dump(folder / "manifest.json", m)
            except BaseException as e:
                m["status"] = "INTERRUPTED"
                m["error"] = str(e)[:500]
                util.dump(folder / "manifest.json", m)
                raise
            finally:
                del ret
                release_cuda_cache()


def judge_settings(a, client):
    return {
        "model_digest": client.meta["digest"],
        "model_family": client.meta.get("details", {}).get("family"),
        "num_ctx": a.num_ctx,
        "max_output": a.max_output,
        "seed": a.seed,
        "thinking": False,
        "budget_tokenizer": client.meta.get("budget_tokenizer"),
        "prompt_hash": util.sha(judging.SYSTEM.encode()),
        "judge_code_hash": judge_code_hash(),
        "policy_hash": util.sha(dataset.POLICY.read_bytes()),
        "ollama_version": client.meta["ollama_version"],
    }


def judge_code_hash():
    return util.sha(b"".join(
        (ROOT / filename).read_bytes() for filename in ("judging.py", "rag_generation.py")
    ))


def assess(record, client, corpus):
    r = {"id": record["id"], "output": None, "calls": [], "error": None}
    try:
        v = call(
            client,
            judging.SYSTEM,
            judging.payload(record, corpus),
            judging.schema(record["output"], record["gold"]),
            r["calls"],
            "judge",
        )
        r["raw_output"] = v
        judging.validate(v, record["output"], record["gold"])
        r["output"], r["objective_audit"] = judging.adjudicate(v, record, corpus)
    except Exception as e:
        r["error"] = failure(e)
    return r


def calibrate(a):
    cases = util.jsonl(JUDGE_CHALLENGES)
    client = Ollama(a)
    corpus = {d["id"]: d["text"] for d in util.sections(dataset.POLICY)[1]}
    ident = {
        "settings": judge_settings(a, client),
        "challenge_hash": util.sha(JUDGE_CHALLENGES.read_bytes()),
    }
    folder = Path(a.out)
    with storage.exclusive(folder):
        m = storage.initialize(folder, ident, a.resume)
        ids = [r["id"] for r in cases]
        prior = storage.records(folder, ids)
        done = {r["id"] for r in prior}
        progress = Progress("CALIBRATE" if "challenge_hash" in ident else "JUDGE", len(ids), len(done), sum(bool(r.get("error")) for r in prior))
        for c in cases:
            if c["id"] not in done:
                progress.start(c["id"])
                r = assess(c, client, corpus)
                util.dump(folder / "records" / f"{c['id']}.json", r)
                progress.finish("ERROR" if r["error"] else "SCORED", error=bool(r["error"]))
        rows = storage.records(folder, ids)
        result = {
            "settings": ident["settings"],
            "challenge_hash": ident["challenge_hash"],
            **judging.calibration_summary(cases, rows),
            "meaning": "Synthetic sanity check only; not independently expert-calibrated",
        }
        storage.export(folder, rows)
        util.dump(folder / "calibration.json", result)
        m["status"] = "COMPLETED"
        util.dump(folder / "manifest.json", m)
        print(json.dumps(result, indent=2))
        if not result["passed"]:
            raise ValueError(
                "Judge failed sanity gate. Do not use it to claim semantic quality."
            )


def judge(a):
    source = Path(a.run)
    m = storage.read(source / "manifest.json")
    if m.get("status") != "COMPLETED":
        raise ValueError("Generator run incomplete")
    if util.sha((source / "predictions.jsonl").read_bytes()) != m["predictions_hash"]:
        raise ValueError("Generator predictions changed")
    if util.sha(dataset.POLICY.read_bytes()) != m["identity"]["policy_hash"]:
        raise ValueError("Policy changed")
    client = Ollama(a)
    judge_relationship = judging.independent(m["runtime"], client.meta)
    cal = storage.read(Path(a.calibration) / "calibration.json")
    settings = judge_settings(a, client)
    if (
        not cal["passed"]
        or cal["settings"] != settings
        or cal["challenge_hash"]
        != util.sha(JUDGE_CHALLENGES.read_bytes())
    ):
        raise ValueError(
            "Passing calibration with identical judge settings is required"
        )
    allrows = util.jsonl(source / "predictions.jsonl")
    rows = [r for r in allrows if r.get("output") and (not r.get("error"))]
    ids = [r["id"] for r in rows]
    corpus = {d["id"]: d["text"] for d in util.sections(dataset.POLICY)[1]}
    for r in rows:
        client.budget(judging.SYSTEM, judging.payload(r, corpus), a.max_output)
    folder = Path(a.out)
    with storage.exclusive(folder):
        ident = {
            "settings": settings,
            "generator_fingerprint": m["fingerprint"],
            "predictions_hash": m["predictions_hash"],
            "eligible_ids": ids,
            "calibration_hash": storage.digest(cal),
            "challenge_hash": cal["challenge_hash"],
        }
        jm = storage.initialize(folder, ident, a.resume)
        prior = storage.records(folder, ids)
        done = {r["id"] for r in prior}
        progress = Progress("CALIBRATE" if "challenge_hash" in ident else "JUDGE", len(ids), len(done), sum(bool(r.get("error")) for r in prior))
        jm.update(
            status="RUNNING", runtime=client.meta, judge_relationship=judge_relationship
        )
        util.dump(folder / "manifest.json", jm)
        for r in rows:
            if r["id"] in done:
                continue
            progress.start(r["id"])
            j = assess(r, client, corpus)
            util.dump(folder / "records" / f"{r['id']}.json", j)
            progress.finish("ERROR" if j["error"] else "SCORED", error=bool(j["error"]))
        judged = storage.records(folder, ids)
        storage.export(folder, judged)
        util.dump(
            folder / "summary.json",
            summarize(
                allrows,
                len(m["identity"]["scenario_ids"]),
                {r["id"]: r for r in judged},
            ),
        )
        jm["status"] = "COMPLETED"
        jm["predictions_hash"] = util.sha((folder / "predictions.jsonl").read_bytes())
        util.dump(folder / "manifest.json", jm)


def report(a):
    root = Path(a.out)
    destination = Path(a.report_out) if getattr(a, "report_out", None) else root
    folders = sorted((p.parent for p in root.glob("*/manifest.json")))
    if not folders:
        raise ValueError("No runs found")
    plan = storage.read(root / "experiment.json")
    if set(plan["planned_arms"]) != {f.name for f in folders}:
        raise ValueError("Missing planned arms; experiment is incomplete")
    results = []
    key = None
    judge_key = None
    vector_key = None
    data_hash = util.sha(dataset.DATA.read_bytes())
    challenge_hash = util.sha(JUDGE_CHALLENGES.read_bytes())
    current_judge_code = judge_code_hash()
    judge_prompt_hash = util.sha(judging.SYSTEM.encode())
    for f in folders:
        m = storage.read(f / "manifest.json")
        ident = m["identity"]
        if "mode" not in ident:
            continue
        if m["status"] != "COMPLETED":
            raise ValueError(
                f"Incomplete run {f.name}; do not report as completed comparison"
            )
        check = {
            k: ident[k]
            for k in (
                "code_hash",
                "policy_hash",
                "data_hash",
                "model_digest",
                "seed",
                "num_ctx",
                "max_output",
                "split",
                "scenario_ids",
                "packages",
                "ollama_version",
                "budget_tokenizer",
                "k",
                "device",
            )
        }
        check["dependencies"] = ident.get("dependencies", {})
        if key is not None and check != key:
            raise ValueError(
                "Incompatible run identities; compare only explicitly controlled experiments"
            )
        key = check
        vector = ident.get("vector_db")
        if vector is not None:
            if vector_key is not None and vector != vector_key:
                raise ValueError("Incompatible vector database identities")
            vector_key = vector
        if util.sha((f / "predictions.jsonl").read_bytes()) != m["predictions_hash"]:
            raise ValueError("Predictions checksum changed")
        rows = util.jsonl(f / "predictions.jsonl")
        if [r["id"] for r in rows] != ident["scenario_ids"]:
            raise ValueError("Missing/duplicate/out-of-order scenario IDs")
        judgments = None
        judge_current = None
        if getattr(a, "judges", None):
            jf = Path(a.judges) / f.name
            if not (jf / "manifest.json").exists():
                raise ValueError(f"Missing judge run for {f.name}")
            jm = storage.read(jf / "manifest.json")
            ji = jm["identity"]
            if (
                jm["status"] != "COMPLETED"
                or ji["generator_fingerprint"] != m["fingerprint"]
                or ji["predictions_hash"] != m["predictions_hash"]
            ):
                raise ValueError("Judge/source mismatch or incomplete judge run")
            if (
                util.sha((jf / "predictions.jsonl").read_bytes())
                != jm["predictions_hash"]
            ):
                raise ValueError("Judge predictions checksum changed")
            if judge_key is not None and ji["settings"] != judge_key:
                raise ValueError(
                    "Different judge settings cannot be pooled in this comparison"
                )
            judge_key = ji["settings"]
            jr = util.jsonl(jf / "predictions.jsonl")
            eligible = [r["id"] for r in rows if r.get("output") and not r.get("error")]
            if [r["id"] for r in jr] != ji["eligible_ids"] or ji["eligible_ids"] != eligible:
                raise ValueError("Judge record IDs mismatch")
            judgments = {r["id"]: r for r in jr}
            judge_current = (
                ji.get("challenge_hash") == challenge_hash
                and bool(ji.get("calibration_hash"))
                and ji["settings"].get("judge_code_hash") == current_judge_code
                and ji["settings"].get("prompt_hash") == judge_prompt_hash
                and ji["settings"].get("policy_hash") == ident["policy_hash"]
            )
        split = ident["split"]
        split_complete = (
            [r["id"] for r in dataset.load(split)] == ident["scenario_ids"]
            if ident["data_hash"] == data_hash else None
        )
        results.append(
            {
                "arm": f.name,
                "mode": ident["mode"],
                "strategy": ident["strategy"],
                "split": split,
                "split_complete": split_complete,
                "judge_evaluator_current": judge_current,
                **summarize(rows, len(ident["scenario_ids"]), judgments),
            }
        )
    if not results:
        raise ValueError("No generator runs")
    util.dump(destination / "comparison.json", results)
    lines = [
        "# Controlled RAG comparison",
        "",
        "Action accuracy excludes explanation questions. Semantic scores are optional provisional LLM-judge estimates; inspect coverage. No automatic winner.",
        "",
        "| Arm | Action correct / N | Accuracy | Recall@4 | Unsafe permissions | Errors | p95 seconds | Judge correctness | Judge coverage |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    fmt = lambda x: "N/A" if x is None else f"{x:.3f}"
    for s in results:
        lines.append(
            f"| {s['arm']} | {s['action_correct']}/{s['action_n']} | {fmt(s['action_accuracy'])} | {fmt(s['retrieval']['4']['recall'])} | {s['unsafe_permission']['count']}/{s['unsafe_permission']['denominator']} | {s['errors']} | {fmt(s['timing']['latency_seconds']['p95'])} | {fmt(s['judge']['estimates']['correctness'])} | {fmt(s['judge']['coverage_valid'])} |"
        )
    lines.extend(
        [
            "",
            "## Retrieval (answerable queries only)",
            "",
            "| Arm | Ranked N | Recall@4 | Precision@4 | MRR@4 | nDCG@4 | Final context recall |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for s in results:
        r = s["retrieval"]["4"]
        lines.append(
            f"| {s['arm']} | {s['retrieval_n']} | {fmt(r['recall'])} | {fmt(r['precision'])} | {fmt(r['mrr'])} | {fmt(r['ndcg'])} | {fmt(s['context_reference_recall'])} |"
        )
    lines.extend(
        [
            "",
            "## Generation and citations (judge estimates)",
            "",
            "| Arm | Correctness | Completeness | Groundedness | Citation precision | Citation recall | Task success, all cases |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for s in results:
        j = s["judge"]["estimates"]
        lines.append(
            "| "
            + s["arm"]
            + " | "
            + " | ".join(
                (
                    fmt(j[k])
                    for k in (
                        "correctness",
                        "completeness",
                        "groundedness",
                        "citation_precision",
                        "citation_recall",
                    )
                )
            )
            + " | "
            + fmt(s["semantic_task_success_all"])
            + " |"
        )
    lines.extend(
        [
            "",
            "## Abstention and operations",
            "",
            "| Arm | False answer | False abstention | Wrong action or failure | Error rate | p50 sec | p95 sec |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for s in results:
        lines.append(
            f"| {s['arm']} | {fmt(s['false_answer']['rate'])} | {fmt(s['false_abstention']['rate'])} | {fmt(s['wrong_action_or_failure_rate'])} | {fmt(s['error_rate_processed'])} | {fmt(s['timing']['latency_seconds']['p50'])} | {fmt(s['timing']['latency_seconds']['p95'])} |"
        )
    lines.extend(
        [
            "",
            "Raw JSON includes @1/@8, denominators, generator tokens, judge tokens/latency and failures. Local hardware/electricity cost is not measured. All dense branches use Qdrant with the same embedding and section grouping.",
        ]
    )
    (destination / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    pilot.write(destination, results)
    print(destination / "comparison.md")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    ps = []
    for command in ("preflight", "run"):
        r = sub.add_parser(command)
        r.add_argument(
            "--modes", nargs="+", choices=MODES, default=["bm25", "hybrid", "oracle"]
        )
        r.add_argument(
            "--strategy", choices=["direct", "evidence-first", *PILOT_PROTOCOLS], default="direct"
        )
        r.add_argument(
            "--split", choices=["validation", "test", "all"], default="validation"
        )
        r.add_argument("--limit", type=int)
        r.add_argument("--k", type=int, default=4)
        r.add_argument("--device", default="cuda")
        r.add_argument("--extraction-output", type=int, default=2048)
        r.add_argument("--out", required=command == "run")
        r.add_argument("--qdrant-url", default="http://localhost:6333")
        ps.append((r, "qwen3:8b", 16384, 1536))
    c = sub.add_parser("calibrate")
    c.add_argument("--out", required=True)
    ps.append((c, "qwen3:32b", 32768, 4096))
    j = sub.add_parser("judge")
    j.add_argument("--run", required=True)
    j.add_argument("--out", required=True)
    j.add_argument("--calibration", required=True)
    ps.append((j, "qwen3:32b", 32768, 4096))
    for r, model, ctx, output in ps:
        r.add_argument("--budget-tokenizer", choices=["auto", "byte"], default="auto")
        r.add_argument("--model", default=model)
        r.add_argument("--num-ctx", type=int, default=ctx)
        r.add_argument("--max-output", type=int, default=output)
        r.add_argument("--url", default="http://localhost:11434")
        r.add_argument("--timeout", type=int, default=600)
        r.add_argument("--allow-cpu", action="store_true")
        r.add_argument("--resume", action="store_true")
        r.add_argument("--seed", type=int, default=42)
    r = sub.add_parser("report")
    r.add_argument("--out", required=True)
    r.add_argument("--judges")
    r.add_argument("--report-out")
    a = p.parse_args()
    if hasattr(a, "num_ctx") and (
        a.num_ctx < 4096 or a.max_output < 1 or a.timeout < 1
    ):
        p.error("Invalid context/output/timeout")
    if hasattr(a, "k") and (
        a.k < 1
        or a.k > 8
        or (a.limit is not None and a.limit < 1)
        or (len(a.modes) != len(set(a.modes)))
    ):
        p.error("k must be 1..8, limit positive, modes unique")
    if dataset.POLICY != cfg.BUILTIN_POLICY:
        if "policy" in getattr(a,"modes",[]) or getattr(a,"strategy",None) in PILOT_PROTOCOLS:
            p.error("The policy retriever and pilot strategies are IssuePilot-specific. For another policy use bm25/dense/hybrid with --strategy direct or evidence-first.")
        if a.command in {"calibrate","judge"} and "judge_challenges" not in cfg.RAG:
            p.error("Supply gold judge_challenges for the custom policy before calibration.")
    if a.command == "validate":
        print(json.dumps(dataset.validate(), indent=2))
    elif a.command in ("run", "preflight"):
        run(a, a.command == "preflight")
    elif a.command == "calibrate":
        calibrate(a)
    elif a.command == "judge":
        judge(a)
    else:
        report(a)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
