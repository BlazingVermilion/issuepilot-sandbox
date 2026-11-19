# Run the IssuePilot AI benchmark

This is an offline CLI, separate from the reviewer console and Docker application. No GitHub token, PostgreSQL, n8n or Snowflake account is required. The default model is `qwen3:8b` on Ollama. An evaluator can supply a different model, dataset, or both without changing application code. The benchmark and the app read **the same** [normative policy](../issuepilot/policies/runtime/issuepilot_policy.md). A run creates local evidence, not issues or external actions.

| Layer | Benchmark input | Reference, withheld from prediction | Main outputs |
|---|---|---|---|
| A · category classification | The source `input` (title and description as reported), one issue per prediction | `label` used for scoring; `issue_number` for identity; source `messages` excluded from inference | Accuracy, macro-F1, per-class precision/recall/F1/support, confusion, abstentions, coverage, errors, latency and tokens |
| B · policy retrieval and generation | Scenario `query` and `task` + policy sections | `relevant`, `expected_action`, `reference_claims` / `reference_answer`; only `oracle` deliberately sees reference section IDs | Recall/Precision/MRR/nDCG at k, complete context, decision and abstention metrics, citation checks, latency/tokens; calibrated judge required for semantic scores |

The pinned VS Code [corpus](https://huggingface.co/datasets/Tilakoid/vscode-bug-feature-triage) has **1,993** issues (1,593 train, 200 validation, 200 test). These are inference splits; the runner does not train. RAG uses **52 policy scenarios** (36 validation, 16 test) with evidence/action references. The issue corpus has no gold policy sections. An all-split metric is descriptive, not an independent test score. Dataset and policy hashes are recorded. [Targets](EVALUATION_TARGETS.md) and [historical selection](../issuepilot/docs/ai-selection.md) describe what is and is not demonstrated.

## Score the entire corpus as one test split

From the Git root, run `py -3.12 demo.py evaluation-data`. This verifies the pinned
1,593/200/200 original splits and creates the ignored files `ai-bench/data/full-corpus/all-1993.jsonl`
and `profile.json`. Labels are for scoring only; neither file is mounted into the demo.
Use the benchmark environment described below, start Ollama and run from **ai-bench/**:

```powershell
$py = (Resolve-Path .\.venv\Scripts\python.exe).Path
& $py bench.py --dataset-config data/full-corpus/profile.json check-data
& $py bench.py --dataset-config data/full-corpus/profile.json classify --modes none --split test --model qwen3:8b --num-ctx 16384 --max-output 128 --out runs/full-1993 --resume
if ($LASTEXITCODE -ne 0) { throw 'Scoring was interrupted; resume with the same settings.' }
& $py bench.py --dataset-config data/full-corpus/profile.json classify-report --out runs/full-1993
```

Expected input: `{'test': 1993}`. This is full-corpus evaluation, not 1,993 unseen test examples;
200 issues were in the original held-out split. RAG retrieval uses separate gold policy scenarios.
The default profile with `--split all` is an alternative and also covers all 1,993.

## 1. Start from a clean checkout

From the Git root, use Python 3.12. In Windows PowerShell:

```powershell
cd ai-bench
py -3.12 -m venv .venv
$py = (Resolve-Path .\.venv\Scripts\python.exe).Path
& $py -m pip install -r requirements.txt
& $py bench.py doctor
& $py bench.py --dataset-config examples/local/profile.json check-data
& $py bench.py fetch
& $py bench.py check-data
```

On Linux/macOS use `cd ai-bench`, `python3.12 -m venv .venv`, `source .venv/bin/activate`, `python -m pip install -r requirements.txt`. Use `python` in place of `& $py` below. Model tags, CLI flags and output paths are identical. In a new PowerShell window reassign `$py`. Do not copy shell prompt markers. Stop at an error before starting the next command.

`fetch` downloads the three public UTF-8 JSONL files at the pinned commit and checks SHA-256 **before** accepting them. If you already have the exact files, copy train.jsonl, validation.jsonl and test.jsonl into `ai-bench/data/` and run `check-data`. Do not export via Excel or resave the pinned files. No downloads are needed for the sample profile, the bundled policy, scenarios or offline tests. Run this next for a quick local check, without an LLM:

```powershell
& $py bench.py --dataset-config examples/local/profile.json classify --modes rules --split validation --out runs/sample-class
& $py bench.py --dataset-config examples/local/profile.json classify-report --out runs/sample-class
& $py bench.py --dataset-config examples/local/profile.json retrieval --modes bm25 --split validation --out runs/sample-retrieval
```

## 2. Run the two selected architectures with your model

Start Ollama on the host and select an **installed, explicit model:tag that supports chat and JSON schema output**. If needed, install the tag with `ollama pull`. Keep one model per output directory. The numbers for Qwen do not transfer to another tag or digest. Client checks the installed model, declared context when available, warm-up and runtime device. Structured-output failures count as errors. GPU is required by default; on a CPU-only machine add `--allow-cpu` to commands that call Ollama, and use `--device cpu` for local encoders. Lexical retrieval and rules do not need CUDA.

```powershell
ollama list
$model = 'qwen3:8b'               # replace with an installed model:tag
$ollama = 'http://localhost:11434' # change if Ollama is on another trusted host
# Selected classifier: Qwen receives the report directly, without retrieved policy.
& $py bench.py classify --modes rules none --split validation --model $model --url $ollama --num-ctx 16384 --max-output 128 --out runs/class-val
& $py bench.py classify-report --out runs/class-val
# Selected RAG: topic-weighted BM25 + required policy dependencies + refined generator.
& $py bench.py rag run --modes policy --strategy pilot-refined --k 4 --split validation --model $model --url $ollama --num-ctx 8192 --max-output 512 --out runs/rag-val
& $py bench.py rag report --out runs/rag-val
# Retrieval alone: no Ollama, judge or Qdrant for BM25 / policy.
& $py bench.py retrieval --modes bm25 policy --split test --out runs/retrieval-test
```

Use `--limit 3` and a separate output directory for a smoke test. For all 1,993 classification predictions use `--split all`; for independent test results use `--split test`, fresh output directories for each layer, and freeze decisions made on validation first. RAG `--split all` is 52 scenarios. `rag run` already does preflight. If interrupted, repeat the *entire original run command* with `--resume` appended; if inputs/settings/digest changed use a new output directory. Use `ollama ps` while inference is running to inspect GPU offload. The tag, digest and settings are in the arm manifest. Unsupported tokenizer tags use a conservative UTF-8 byte budget plus a template reserve, never silent clipping. If a model cannot fit the benchmark context, choose a supported context/model and record this as a separate experiment.

Each run produces `<out>/<arm>/manifest.json`, `predictions.jsonl`, per-case records and a summary; reports write `comparison.md` and `comparison.json`. Retrieval-only produces `<out>/<mode>.json`. Reports retain errors, abstentions and empty denominators. Read `n`, errors, split, and judge coverage before interpreting scores. Raw records can contain complete input/prompt text; keep proprietary datasets and runs out of Git.

## 3. Use another dataset (without changing Python)

Copy [examples/local](examples/local/) into your own local folder. Pass **`--dataset-config path/to/profile.json` before the command**, for every run/report on that dataset. Profile paths are relative to the JSON file. You may replace only classification or only RAG; an omitted layer uses the defaults. Example with remapped issue columns:

```json
{
  "classification": {
    "splits": {"validation":"validation.jsonl", "test":"test.jsonl"},
    "fields": {"id":"ticket", "text":"description", "label":"kind"}
  },
  "rag": {"scenarios":"scenarios.jsonl"}
}
```

One JSON object per line, UTF-8. An issue example: `{"ticket":91,"description":"Title: Cannot save\n\nDescription:\nThe editor crashes on save.","kind":"bug"}`. Supply a stable positive integer ID unique across splits and a single nonempty text column. If title/body are separate upstream, join **only** title and description into that column before export. Do not append past labels, replies, resolution or assistant messages. Duplicate IDs, an absent requested split, unknown labels, unsafe scenario IDs and unknown policy references fail validation. The runner cannot detect paraphrased duplicates or gold already embedded in the source text; audit those before reporting results.

For a different taxonomy add `"classes":{"incident":"Existing function fails","request":"A new function is requested","usage":"User asks how to use a function"}` **inside classification**, and use these exact labels in the rows. Run `--modes none` (or a compatible policy arm). The bundled rules are specific to the default definitions. The sample demonstrates mapped columns; swapping the offline taxonomy does not expand the app's binary classifier or authorize additional labels.

For RAG copy a row from [the sample](examples/local/scenarios.jsonl). Supply a unique `id`, a `family` restricted to one split, `split` (validation/test), `query`, `task` (action/explanation), boolean `answerable`, list `relevant`, `reference_claims`, `reference_answer`, and `expected_action` (ALLOW/BLOCK/NEED_FACTS for actions, null for explanations). For a question outside the policy, `answerable=false`, `relevant=[]`, and explicitly state the correct refusal in the references. Do not copy the answer into the query. Add provenance/version to make annotation changes traceable. Optionally point `rag.policy` to another Markdown file, with unique headings like `## IP-OPS-001 — Ticket intake`, and `rag.dependencies` to an object such as `{"IP-OPS-002":["IP-OPS-001"]}`. On another policy use generic `bm25`, `dense` or `hybrid` plus `--strategy direct`/`evidence-first`; the topic lexicon and timing rules in `policy`/`pilot-refined` belong to IssuePilot. Use a benchmark-specific `rag.judge_challenges` for a custom policy.

```powershell
$cfg = 'examples/local/profile.json'   # change to your profile
& $py bench.py --dataset-config $cfg check-data
& $py bench.py --dataset-config $cfg classify --modes none --split validation --model $model --out runs/custom-class
& $py bench.py --dataset-config $cfg classify-report --out runs/custom-class
& $py bench.py --dataset-config $cfg rag run --modes bm25 --strategy direct --split validation --model $model --out runs/custom-rag
& $py bench.py --dataset-config $cfg rag report --out runs/custom-rag
```

## 4. Compare architectures or add a judge

The shipped lexical profile does not require a vector service. For dense, hybrid or hybrid-rerank comparisons, install PyTorch matching your hardware in this **benchmark** environment ([official selector](https://pytorch.org/get-started/locally/)), then:

```powershell
& $py -m pip install -r requirements-retrieval.txt
docker compose up -d qdrant
& $py -c "import torch; print(torch.cuda.is_available())"
& $py bench.py rag run --modes bm25 dense hybrid hybrid-rerank full oracle none --strategy direct --split validation --model $model --device cuda --out runs/arms
& $py bench.py rag report --out runs/arms
```

Run Compose in `ai-bench/`; Qdrant is bound to localhost:6333. For CPU replace `cuda` with `cpu` and append `--allow-cpu` if the generator is also on CPU. First dense runs download the pinned encoder/reranker. `none` is the no-evidence control; `full` uses all sections; `oracle` uses reference section IDs and cannot serve real predictions. Compare like splits, token budgets and model digests.

For semantic quality, run the judge **after** generation on a different model. Configure an installed tag/host with sufficient RAM/VRAM; for example use your existing `qwen2.5:7b`, or use a larger model on a separate host. Calibration must pass before scores are accepted. A calibration failure is a result, not a reason to skip the gate.

```powershell
$judge = 'qwen2.5:7b'
$judgeHost = 'http://localhost:11434'
& $py bench.py rag calibrate --model $judge --url $judgeHost --num-ctx 16384 --max-output 4096 --out runs/judge-cal
if ($LASTEXITCODE -ne 0 -or -not (Get-Content -Raw runs/judge-cal/calibration.json | ConvertFrom-Json).passed) { throw 'Judge calibration did not pass' }
& $py bench.py rag judge --run runs/rag-val/policy--pilot-refined --model $judge --url $judgeHost --num-ctx 16384 --max-output 4096 --calibration runs/judge-cal --out runs/judges/policy--pilot-refined
& $py bench.py rag report --out runs/rag-val --judges runs/judges --report-out runs/judged-report
```

The example judges one arm; for a multi-arm report use the same calibrated judge settings for every completed arm. Judge tokens/latency differ from serving latency. See [evaluation targets](EVALUATION_TARGETS.md) for denominators and limitations.

## 5. Optionally try the evaluated model in the application

`--model` changes the **benchmark only**. The default serving profile remains pinned to the measured Qwen digest. To try another compatible tag in an **isolated demo**, obtain its full digest locally (the short ID from `ollama list` is insufficient):

```powershell
(Invoke-RestMethod 'http://localhost:11434/api/tags').models | Select-Object name,digest
```

In the app's existing `.env.replay` (dataset replay) or `.env` (ordinary demo), set `IP_AI_PROFILE=experimental`, `IP_MODEL=your-tag:variant`, and `IP_MODEL_DIGEST=<64 lowercase hex characters>`. Preserve all other settings. Replay: run `py -3.12 scripts/demo.py up` from `issuepilot/`. Standard demo: `docker compose up -d --build --force-recreate --wait api worker`. Both stages use the chosen model; the profile and digest accompany its telemetry. Experimental serving requires demo mode, disabled external writes, and a model supporting the stage context windows (16,384 and 8,192 tokens). Test in a separate run when comparing models. To return to the measured profile restore `IP_AI_PROFILE=selected`, `IP_MODEL=qwen3:8b` and empty `IP_MODEL_DIGEST`. An alternative model does not inherit the historical Qwen metrics.

For the default directory layout on Windows, the optional RUN_JUDGE_QWEN25.ps1 wrapper calibrates, judges every recorded arm in runs/rag-val and generates the report. The explicit commands above work for any supported judge tag.
