"""Run IssuePilot's offline classification and RAG experiments. No database or reviewer token."""
import os
import sys
from pathlib import Path

HELP = """python bench.py [--dataset-config path/to/profile.json] COMMAND [options]
  fetch                Fetch and verify pinned issues, or validate local issue files
  check-data           Validate both datasets and policy references (no model)
  classify             Run classification arms; use classify --help for options
  classify-report      Compare completed classification arms (--out RUN)
  retrieval            Measure ranking/context without a generator
  rag                  validate, preflight, run, report, calibrate, judge
  probe                Inspect the generator output protocol
  doctor               Run built-in contract tests (no model)"""

def main():
    if sys.argv[1:2] == ["--dataset-config"]:
        if len(sys.argv)<4: raise ValueError("Expected --dataset-config FILE COMMAND")
        os.environ["ISSUEPILOT_BENCH_CONFIG"] = str(Path(sys.argv[2]).resolve())
        del sys.argv[1:3]
    if len(sys.argv)<2 or sys.argv[1] in {"-h","--help"}:
        print(HELP); return
    command = sys.argv.pop(1)
    if command in {"fetch","check-data"}:
        import classification, dataset
        spec,rows = classification.source(download=command=="fetch")
        print("Verified",len(rows),"issues:",spec["splits"])
        if command=="check-data": print("RAG:",dataset.validate())
    elif command == "classify":
        import classification
        classification.main(sys.argv[1:])
    elif command == "rag":
        import lab
        lab.main()
    elif command == "retrieval":
        import runpy
        runpy.run_module("retrieval_only", run_name="__main__")
    elif command == "classify-report":
        import argparse, classification
        parser=argparse.ArgumentParser(); parser.add_argument("--out",required=True)
        classification.report(parser.parse_args(sys.argv[1:]).out)
    elif command == "probe":
        import protocol_probe
        protocol_probe.main(sys.argv[1:])
    elif command == "doctor":
        import subprocess
        root=Path(__file__).resolve().parent
        env=dict(os.environ); env.pop("ISSUEPILOT_BENCH_CONFIG",None)
        subprocess.run([sys.executable,"-m","unittest","discover","-s","tests","-v"],check=True,cwd=root,env=env)
    else: raise ValueError("Unknown command: "+command)

if __name__=="__main__":
    try: main()
    except Exception as e:
        print(f"ERROR: {e}",file=sys.stderr); sys.exit(1)
