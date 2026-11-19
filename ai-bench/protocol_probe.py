"""Live schema probes with durable diagnostics, separate from quality scores."""

import argparse
import json
from pathlib import Path

import jsonschema

import classification
import contracts
import retrieval
from client import CallError, Ollama


def checks(constraint_challenges=False):
    result = [("category", classification.SCHEMA, {"category": "bug"}, "exact")]
    for task, state in [
        ("action", "ALLOW"),
        ("action", "BLOCK"),
        ("action", "NEED_FACTS"),
        ("action", "NO_POLICY"),
        ("explanation", "EXPLANATION"),
    ]:
        result.append(("decision-" + state, contracts.decision_schema(task),
                       {"decision": state}, "exact"))
    for state in ["ALLOW", "BLOCK", "NEED_FACTS", "EXPLANATION", "NO_POLICY"]:
        target = ({"reason": "No applicable policy."} if state == "NO_POLICY" else
                  {"claims": [{"text": "A test claim.", "citations": ["TEST-1"]}]})
        result.append(("payload-" + state, contracts.payload_schema(["TEST-1"], state),
                       target, "exact"))
    if constraint_challenges:
        # Deliberately invalid target: two allowed IDs keep a one-item cap from
        # accidentally passing a challenge intended for citation uniqueness.
        target = {"claims": [{"text": "A test claim.",
                              "citations": ["TEST-1", "TEST-1"]}]}
        result.append(("payload-unique-citations",
                       contracts.payload_schema(["TEST-1", "TEST-2"], "BLOCK"),
                       target, "unique_citations"))
    return result


def validate_response(value, schema, target, expectation, name):
    jsonschema.validate(value, schema)
    if expectation == "unique_citations":
        # The requested object intentionally violates uniqueItems. Accept only
        # removal of repeated IDs: no altered claims or invented citation IDs.
        expected = {"claims": [{**claim, "citations": list(dict.fromkeys(claim["citations"]))}
                               for claim in target["claims"]]}
    else:
        expected = target
    if value != expected:
        raise ValueError("Protocol probe mismatch: " + name)


def diagnostic(error, stage):
    if isinstance(error, CallError):
        kind = error.kind
    elif isinstance(error, jsonschema.ValidationError):
        kind = "SCHEMA_INVALID"
    elif not isinstance(error, Exception):
        kind = "INTERRUPTED"
    else:
        kind = "PROBE_ERROR"
    result = {"kind": kind, "stage": stage, "type": type(error).__name__,
              "detail": str(error) or type(error).__name__}
    if isinstance(error, CallError):
        result["meta"] = error.meta
    return result


def main(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="qwen3:8b")
    p.add_argument("--url", default="http://localhost:11434")
    p.add_argument("--num-ctx", type=int, default=16384)
    p.add_argument("--max-output", type=int, default=512)
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--budget-tokenizer", choices=["auto", "byte"], default="auto")
    p.add_argument("--allow-cpu", action="store_true")
    p.add_argument("--constraint-challenges", action="store_true",
                   help="Also challenge citation uniqueness with an intentionally invalid requested object.")
    p.add_argument("--out", default="runs/protocol-probe.json")
    a = p.parse_args(argv)
    if a.num_ctx < 4096 or a.max_output < 1 or a.timeout < 1:
        p.error("Invalid context/output/timeout")

    result = {
        "model": None,
        "requested": {key: getattr(a, key) for key in (
            "model", "num_ctx", "max_output", "timeout", "seed",
            "budget_tokenizer", "allow_cpu", "constraint_challenges")},
        "scope": ("Sampled schema conformance only; not semantic evaluation or proof "
                  "that every grammar constraint is enforced. Constraint challenges "
                  "use intentionally invalid requested objects."),
        "checks": [],
        "passed": False,
        "status": "INITIALIZING",
    }
    output = Path(a.out)
    retrieval.dump(output, result)
    stage = "startup"
    entry = None
    try:
        client = Ollama(a)
        result["model"] = client.meta
        result["status"] = "RUNNING"
        retrieval.dump(output, result)
        for name, schema, target, expectation in checks(a.constraint_challenges):
            entry = {"name": name, "target": target, "schema": schema,
                     "expectation": expectation, "passed": False, "status": "RUNNING"}
            result["checks"].append(entry)
            stage = "chat"
            retrieval.dump(output, result)
            value, meta = client.chat(
                "Return the requested object exactly. This is a protocol probe.",
                json.dumps(target), schema)
            entry.update(response=value, meta=meta)
            stage = "validation"
            validate_response(value, schema, target, expectation, name)
            entry.update(passed=True, status="PASSED")
            retrieval.dump(output, result)
            print(name, "PASS", flush=True)
        result.update(passed=True, status="PASSED")
    except BaseException as error:
        failure = diagnostic(error, stage)
        status = "INTERRUPTED" if not isinstance(error, Exception) else "FAILED"
        result.update(error=failure, status=status)
        if entry is not None:
            entry.update(error=failure, status=status)
            if isinstance(error, CallError):
                entry["meta"] = error.meta
        raise
    finally:
        retrieval.dump(output, result)
    print(a.out)
