"""Refinements that preserve the single-call decision states."""

import re

import rag_generation


UNKNOWN_FACT = re.compile(
    r"\b(?:unknown|unverified|not yet known|not yet established)\b"
    r"|\b(?:nobody|no one)\s+has\s+(?:confirmed|established|checked)\s+whether\b"
    r"|\b(?:does not|doesn't)\s+(?:establish|confirm|show)\s+whether\b",
    re.I,
)


def prompt(query, task, docs, timing):
    result = rag_generation.user_prompt(query, task, docs, timing)
    if task == "action" and UNKNOWN_FACT.search(query):
        result += (
            "\nDECISION CHECK: The question expresses an unresolved fact. Determine whether "
            "the applicable policy requires this fact. If it does and no independent failure "
            "is established, select NEED_FACTS. In the claims, identify the fact to confirm and "
            "why it is needed. Do not assert an outcome that depends on the missing fact.\n"
        )
    if timing is not None:
        result += "\nThe answer will include the computed deadline. Explain the remaining conditions.\n"
    return result


def wire_schema(docs, task):
    schema = rag_generation.wire_schema(docs, task)
    if docs:
        citations = schema["properties"]["claims"]["items"]["properties"]["citations"]
        citations["maxItems"] = 1
    return schema
