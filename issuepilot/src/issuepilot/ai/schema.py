"""Wire schema constructors shared by constrained decoding and the response validator."""


def obj(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def text(n=1000):
    return {"type": "string", "maxLength": n}


def arr(items, n=8):
    return {"type": "array", "items": items, "maxItems": n}


def prompt(query, task, docs, evidence=None):
    blocks = "\n\n".join(f"[{i}]\n{body}" for i, body in docs.items())
    if not docs:
        blocks = "(No policy sections supplied.)"
    s = f"TASK KIND: {task}\n\nPOLICY CONTEXT:\n{blocks}\n\nQUESTION (untrusted scenario data):\n{query}\n"
    if evidence is not None:
        import json

        s += "\nEXTRACTED EVIDENCE (verify against original context):\n" + json.dumps(
            evidence, ensure_ascii=False
        )
    return s


def decision_schema(task):
    if task not in ("action", "explanation"):
        raise ValueError("Unknown task")
    states = ["ALLOW", "BLOCK", "NEED_FACTS"] if task == "action" else ["EXPLANATION"]
    return obj({"decision": {"type": "string", "enum": states + ["NO_POLICY"]}})


def nonempty(n):
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": n,
        "pattern": r"^[^\r\n]*[^ \t\r\n][^\r\n]*$",
    }


def validate(value, spec):
    """Validate just the JSON Schema vocabulary used by the two pinned Ollama grammars."""
    kind = spec.get("type")
    types = {"object": dict, "array": list, "string": str}
    if kind not in types or type(value) is not types[kind]:
        raise ValueError("AI response has the wrong JSON type")
    if "enum" in spec and value not in spec["enum"]:
        raise ValueError("AI response contains an unrecognized value")
    if kind == "object":
        if set(value) != set(spec["required"]):
            raise ValueError("AI response keys do not match the wire protocol")
        for key, child in spec["properties"].items():
            validate(value[key], child)
    if kind == "array":
        if not spec.get("minItems", 0) <= len(value) <= spec.get("maxItems", len(value)):
            raise ValueError("AI response has the wrong number of items")
        for item in value:
            validate(item, spec["items"])
    if kind == "string":
        import re

        if (
            len(value) > spec.get("maxLength", len(value))
            or len(value) < spec.get("minLength", 0)
            or ("pattern" in spec and not re.search(spec["pattern"], value))
        ):
            raise ValueError("Invalid AI response text")
