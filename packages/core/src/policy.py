"""Storage-independent acceptance decisions for a completed ledger fact."""

from runner import ESCAPE


DEFAULT_POLICY = [
    {"id": "P01", "when": {"completion": False}, "then": "hold"},
    {"id": "P02", "when": {"conflict": True}, "then": "queue_for_human"},
    {"id": "P03", "when": {"combined": "duplicate_skip"}, "then": "reject"},
    {"id": "P04", "when": {"combined": "no_record"}, "then": "retain_as_evidence"},
    {"id": "P05", "when": {"combined": "needs_review", "review_reasons": ["impact: reference_only (policy pending)"]}, "then": "retain_as_evidence"},
    {"id": "P06", "when": {"combined": "needs_review", "can_ask_now": True}, "then": "ask_now"},
    {"id": "P07", "when": {"combined": "needs_review"}, "then": "queue_for_human"},
    {"id": "P08", "when": {"kind": ("decision", "constraint"), "evidence_source": ("user_tentative", "ai_inference", "observed_output"), "can_ask_now": True}, "then": "ask_now"},
    {"id": "P09", "when": {"kind": ("decision", "constraint"), "evidence_source": ("user_tentative", "ai_inference", "observed_output")}, "then": "queue_for_human"},
    {"id": "P10", "when": {"combined": ("record", "update_record", "deprecate_record")}, "then": "absorb"},
]


def evaluate(context, rules=DEFAULT_POLICY):
    for rule in rules:
        if all(context.get(key) in expected if isinstance(expected, tuple) else context.get(key) == expected
               for key, expected in rule["when"].items()):
            return {"rule_id": rule["id"], "action": rule["then"]}
    return {"rule_id": None, "action": "hold"}


def apply_utterance_status(evidence_source, jev_response):
    if evidence_source != "user_confirmed":
        return evidence_source
    probabilities = {label: jev_response["probabilities"].get(label, 0.0)
                     for label in ("confirmed_decision", "tentative_opinion", "none_or_uncertain")}
    ranked = sorted(probabilities, key=probabilities.get, reverse=True)
    choice = jev_response.get("choice", jev_response.get("value", ranked[0]))
    if (len(ranked) < 2 or probabilities[ranked[0]] - probabilities[ranked[1]] < .15
            or ESCAPE.search(str(choice)) or choice != "confirmed_decision"):
        return "user_tentative"
    return "user_confirmed"
