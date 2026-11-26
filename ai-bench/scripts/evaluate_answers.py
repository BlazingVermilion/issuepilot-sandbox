"""Calibrate a selected judge, judge saved RAG arms, and update their comparison."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ARM = re.compile(r"(?:bm25|dense|hybrid|hybrid-rerank|policy|full|oracle|none)--(?:direct|evidence-first|policy-base|policy-decision|pilot|pilot-refined)")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def bench(*arguments):
    subprocess.check_call(
        [sys.executable, str(ROOT / "bench.py"), *map(str, arguments)], cwd=ROOT
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen2.5:7b", help="Ollama judge tag; benchmark generators are unchanged")
    parser.add_argument("--run-root", type=Path, default=ROOT / "runs")
    parser.add_argument("--source", type=Path, help="Completed generator experiment to rejudge")
    parser.add_argument("--judge-root", type=Path, help="Directory for calibration, judgements and reports")
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--max-output", type=int, default=4096)
    parser.add_argument("--url", default="http://localhost:11434")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if not 4096 <= args.num_ctx <= 32768 or not 1 <= args.max_output < args.num_ctx:
        parser.error("Require 4096 <= num-ctx <= 32768 and 1 <= max-output < num-ctx")

    run_root = (ROOT / args.run_root).resolve()
    source = (ROOT / args.source).resolve() if args.source else run_root / "policy"
    judge_root = (
        (ROOT / args.judge_root).resolve()
        if args.judge_root else source.parent / f"{source.name}-judging"
    )
    calibration = judge_root / "calibration"
    judges = judge_root / "arms"
    arms = read_json(source / "experiment.json")["planned_arms"]
    if not isinstance(arms, list) or not arms:
        raise ValueError("No planned generator arms were found")
    if any(not isinstance(arm, str) or not ARM.fullmatch(arm) for arm in arms):
        raise ValueError("Unexpected generator arm")
    if len(arms) != len(set(arms)):
        raise ValueError("Duplicate planned generator arms")
    for arm in arms:
        if read_json(source / arm / "manifest.json").get("status") != "COMPLETED":
            raise ValueError("Generator arm is incomplete: " + arm)

    # The same settings and interpreter are used for calibration and every arm.
    common = ["--model", args.model, "--url", args.url,
              "--num-ctx", args.num_ctx, "--max-output", args.max_output, "--seed", 42]
    resume = ["--resume"] if args.resume else []
    print(f"Calibrating {args.model} on the synthetic judge challenges...", flush=True)
    bench("rag", "calibrate", "--out", calibration, *common, *resume)
    if read_json(calibration / "calibration.json").get("passed") is not True:
        raise ValueError("Judge calibration failed; scoring has not started")

    for arm in arms:
        print(f"Judging {arm} with {args.model}...", flush=True)
        bench("rag", "judge", "--run", source / arm, "--out", judges / arm,
              "--calibration", calibration, *common, *resume)

    bench("rag", "report", "--out", source, "--judges", judges, "--report-out", judge_root)
    print(f"Judge run completed. Report: {judge_root / 'comparison.md'}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as error:
        print(f"ERROR: Benchmark command failed (exit {error.returncode}).", file=sys.stderr)
        sys.exit(error.returncode)
    except (OSError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
