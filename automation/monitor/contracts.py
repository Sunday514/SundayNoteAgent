"""Shared output contracts. Reports contain findings; only decisions authorize action."""

def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


STR = {"type": "string"}
SOURCE = obj({"location": STR, "quote": STR})
SUMMARY_SCHEMA = obj({k: {"type": "array", "items": STR} for k in
                      ("user_requests", "user_decisions", "assistant_claims", "observations", "inferences", "open_questions")})
FINDING = obj({"title": STR, "reason": STR, "check": STR,
               "evidence": {"type": "array", "minItems": 1, "items": SOURCE}})
DECISION = obj({"question": STR, "options": {"type": "array", "maxItems": 3, "items": STR}})
FEEDBACK = obj({"summary": STR, "findings": {"type": "array", "minItems": 1, "items": FINDING},
                "decision": {"anyOf": [DECISION, {"type": "null"}]}})
FINDING_REF = obj({"direction": STR, "index": {"type": "integer", "minimum": 0}})
HANDOFF = obj({**FEEDBACK["properties"], "findings": {"type": "array", "minItems": 1,
    "items": {"anyOf": [FINDING, FINDING_REF]}}})
SUMMARIES = {"type": "array", "items": obj({"turn_id": STR, "summary": SUMMARY_SCHEMA})}
RESULT_SCHEMA = obj({
    "context_updates": {"type": "array", "items": obj({"key": STR, "value": STR, "source": SOURCE})},
    "summaries": SUMMARIES,
    "checked": {"type": "array", "items": SOURCE},
    "feedback": {"anyOf": [HANDOFF, {"type": "null"}]},
})
CHECKPOINT_SCHEMA = obj({"summaries": SUMMARIES})
CHECK_SCHEMA = obj({"direction": {"type": "string", "enum": ["consistency", "redundancy", "knowledge", "review"]},
                    "target_id": STR, "status": {"type": "string", "enum": ["complete", "partial", "failed"]},
                    "findings": {"type": "array", "items": FINDING},
                    "checked": {"type": "array", "items": SOURCE},
                    "read_versions": {"type": "array", "items": obj({"path": STR, "sha256": STR})},
                    "limitations": {"type": "array", "items": STR}})


def validate(value, schema):
    if "anyOf" in schema:
        for choice in schema["anyOf"]:
            try:
                validate(value, choice)
                return
            except ValueError:
                pass
        raise ValueError("invalid union")
    kind = schema["type"]
    if kind == "null":
        if value is not None:
            raise ValueError("expected null")
    elif kind == "object":
        if not isinstance(value, dict) or set(value) != set(schema["required"]):
            raise ValueError("invalid object")
        for key, sub in schema["properties"].items():
            validate(value[key], sub)
    elif kind == "array":
        if not isinstance(value, list) or len(value) < schema.get("minItems", 0) or (
                "maxItems" in schema and len(value) > schema["maxItems"]):
            raise ValueError("invalid array")
        for item in value:
            validate(item, schema["items"])
    elif kind == "integer":
        if type(value) is not int or value < schema.get("minimum", 0):
            raise ValueError("invalid integer")
    elif not isinstance(value, str) or len(value) > 6000 or ("enum" in schema and value not in schema["enum"]):
        raise ValueError("invalid string")


def validate_feedback(value):
    validate(value, FEEDBACK)
    decision = value["decision"]
    if not value["summary"].strip() or any(not f["reason"].strip() for f in value["findings"]):
        raise ValueError("empty feedback")
    if decision and not decision["question"].strip():
        raise ValueError("empty decision")


def resolve_handoff(feedback, checks):
    """Copy validated child evidence verbatim; selection stays with the parent."""
    if feedback is None:
        return None
    validate(feedback, HANDOFF)
    reports = {c["direction"]: c for c in checks if c["status"] != "failed"}
    findings = []
    for item in feedback["findings"]:
        if "index" in item:
            try:
                item = reports[item["direction"]]["findings"][item["index"]]
            except (KeyError, IndexError):
                raise ValueError("invalid finding reference") from None
        if item not in findings:
            findings.append(item)
    result = {**feedback, "findings": findings}
    validate_feedback(result)
    return result
