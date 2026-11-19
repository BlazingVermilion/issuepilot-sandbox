"""Read a portable experiment profile; paths are relative to the profile, not the shell."""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILTIN_POLICY = ROOT.parent / "issuepilot/policies/runtime/issuepilot_policy.md"
selected = os.environ.get("ISSUEPILOT_BENCH_CONFIG")
PROFILE_PATH = Path(selected).resolve() if selected else None
PROFILE = json.loads(PROFILE_PATH.read_text(encoding="utf-8-sig")) if selected else {}
if not isinstance(PROFILE,dict) or set(PROFILE)-{"classification","rag"}:
    raise ValueError("Profile accepts only classification and rag objects")
BASE = PROFILE_PATH.parent if PROFILE_PATH else ROOT
CLASS = PROFILE.get("classification", {})
RAG = PROFILE.get("rag", {})
if not isinstance(CLASS,dict) or set(CLASS)-{"splits","classes","fields"}:
    raise ValueError("classification accepts splits, classes and fields")
if not isinstance(RAG,dict) or set(RAG)-{"scenarios","policy","dependencies","judge_challenges"}:
    raise ValueError("rag accepts scenarios, policy, dependencies and judge_challenges")
DEFAULT_CLASSES = ["bug","feature-request"]
definitions = CLASS.get("classes")
if definitions is not None and (not isinstance(definitions,dict) or len(definitions)<2 or any(
    not re.fullmatch(r"[A-Za-z0-9][\w -]{0,63}",name) or name.upper() in {"ERROR","ABSTAIN"} or
    not isinstance(description,str) or not description.strip() for name,description in definitions.items())):
    raise ValueError("classes must map at least two distinct label names to nonempty definitions")
if CLASS and ("splits" not in CLASS or not isinstance(CLASS["splits"],dict) or
        not CLASS["splits"] or set(CLASS["splits"])-{"train","validation","test"}):
    raise ValueError("classification.splits must map split names to local JSONL files")
FIELDS = {"id":"issue_number","text":"input","label":"label"} | CLASS.get("fields",{})
if set(FIELDS)!={"id","text","label"} or len(set(FIELDS.values()))!=3 or not all(isinstance(v,str) and v for v in FIELDS.values()):
    raise ValueError("fields must map id, text, label to three different column names")
if FIELDS["text"].lower() in {"label","messages","assistant","target","reference"}:
    raise ValueError("Use issue text, not a label or a conversation containing the answer")
def path(value):
    if not isinstance(value,str) or not value.strip(): raise ValueError("Expected a local path")
    return (BASE/value).resolve()
POLICY = path(RAG["policy"]) if "policy" in RAG else BUILTIN_POLICY
SCENARIOS = path(RAG["scenarios"]) if "scenarios" in RAG else ROOT/"data/scenarios.jsonl"
CHALLENGES = path(RAG["judge_challenges"]) if "judge_challenges" in RAG else ROOT/"data/judge_challenges.jsonl"
