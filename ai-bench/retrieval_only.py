"""Retrieve without loading a generator; reference labels never enter the retriever."""

import argparse, time
from pathlib import Path
import dataset, retrieval as u
from evaluation import mean, quantile, rank_scores

p = argparse.ArgumentParser()
p.add_argument(
    "--modes",
    nargs="+",
    choices=["bm25", "dense", "hybrid", "hybrid-rerank", "policy"],
    default=["bm25"],
)
p.add_argument("--split", choices=["validation", "test"], default="validation")
p.add_argument("--device", default="cuda")
p.add_argument("--out", required=True)
p.add_argument("--qdrant-url", default="http://localhost:6333")
a = p.parse_args()
if "policy" in a.modes and dataset.POLICY != dataset.cfg.BUILTIN_POLICY:
    p.error("Use bm25, dense or hybrid for custom policies")
root = Path(a.out)
if root.exists():
    raise SystemExit("Use a new output directory")
root.mkdir(parents=True)
dataset.validate()
if not dataset.load(a.split): raise ValueError("No scenarios in the requested split")
for mode in a.modes:
    ret = u.Retriever(dataset.POLICY, mode, device=a.device, qdrant_url=a.qdrant_url)
    rows = []
    for c in dataset.load(a.split):
        start = time.perf_counter()
        docs, rank = dataset.assemble(ret, c, mode, 4)
        rows.append(
            {
                "id": c["id"],
                "rank": rank,
                "context_ids": list(docs),
                "gold": c["relevant"],
                "task": c["task"],
                "seconds": time.perf_counter() - start,
            }
        )
    m = {
        str(k): {
            n: mean(rank_scores(r["rank"], r["gold"], k)[n] for r in rows if r["gold"])
            for n in ["recall", "precision", "mrr", "ndcg"]
        }
        for k in [1, 4, 8]
    }
    answerable = [r for r in rows if r["gold"]]
    critical = [
        r for r in answerable
        if r["task"] == "action" and any(i.startswith("IP-CLOSE-") for i in r["gold"])
    ]
    context = {
        "reference_recall": mean(
            len(set(r["gold"]) & set(r["context_ids"])) / len(r["gold"]) for r in answerable
        ),
        "complete_reference_rate": mean(
            int(set(r["gold"]) <= set(r["context_ids"])) for r in answerable
        ),
        "answerable_n": len(answerable),
        "critical_closure_n": len(critical),
        "critical_closure_misses": [
            r["id"] for r in critical if not set(r["gold"]) <= set(r["context_ids"])
        ],
    }
    p95 = quantile([r["seconds"] for r in rows], 0.95)
    u.dump(
        root / (mode + ".json"),
        {
            "rows": rows,
            "metrics": m,
            "context_metrics": context,
            "retrieval_p95_seconds": p95,
            "retriever": ret.meta,
            "policy_hash": u.sha(dataset.POLICY.read_bytes()),
            "data_hash": u.sha(dataset.DATA.read_bytes()),
        },
    )
    print(mode, {"ranking": m, "context": context, "retrieval_p95_seconds": p95})
    del ret
    __import__("lab").release_cuda_cache()
