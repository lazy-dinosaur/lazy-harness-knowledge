"""Source-grounded semantic review; quote validation is input integrity, not a verdict."""
from __future__ import annotations

import hashlib
import json
import math

import source_evidence

VERDICTS = {"compatible", "different_target", "historical_or_scope", "genuine_conflict", "insufficient"}
SOURCE_REVIEW_QUESTION = (
    "Compare the two canonical claims with both supplied source contexts. Before choosing a verdict, decompose "
    "each AND/conjunctive claim into its asserted components and test whether every component on both sides can "
    "be true together. Align the actual entity, operation/output mode, conditions, time/revision, and authority; "
    "read the surrounding source context rather than relying on matching grammar or a model's prior label. "
    "Choose compatible when no pair of same-scope assertions is incompatible, including when one broad claim "
    "entails the narrower claim. Choose different_target only when the sources establish distinct targets. "
    "Choose historical_or_scope only when source context establishes distinct times, conditions, or scope. "
    "Choose genuine_conflict only when you can cite opposite assertions about the same target, operation, "
    "condition, time, and authority; identify both opposed assertions in your explanation. If any required axis "
    "is missing, choose insufficient. Do not infer entity identity from shared subject words. Quote validity is "
    "not semantic support; do not approve a human decision or rewrite facts."
)
SOURCE_REVIEW_CRITERIA = {
    "compatible": "Both source-backed claims can be true together without loss of scope or condition.",
    "different_target": "The source contexts identify distinct entities, operations, or lifecycle targets.",
    "historical_or_scope": "The apparent conflict is explained by source time, revision, condition, or scope.",
    "genuine_conflict": "Both refer to the same target, time, condition, and scope and assert incompatible claims.",
    "insufficient": "The provided evidence does not safely establish one of the distinctions above.",
}


class SourceProvider:
    """Caller-owned project root plus optional explicit immutable import bindings."""
    def __init__(self, host_root, *, bindings=None):
        self.host_root = host_root
        self.bindings = bindings or {}

    def read_fragment(self, fragment):
        return source_evidence.read_fragment_sources(self.host_root, fragment.get("source"),
                                                     bindings=self.bindings)

    def recover_original_context(self, fragment):
        """Read only declared source documents when existing citation anchors fail verification."""
        return source_evidence.read_declared_original_contexts(self.host_root, fragment.get("source"),
                                                               bindings=self.bindings)


def _probability(answer, verdict):
    probs = answer.get("probabilities") if isinstance(answer, dict) else None
    value = probs.get(verdict) if isinstance(probs, dict) else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def review_pair_from_agent(left, right, provider: SourceProvider, *, verdict, explanation, source_quotes,
                           opposed_assertions=None, same_scope=None):
    """Bind an LLM agent's source-grounded semantic adjudication to freshly verified source contexts.

    This is not a user decision and does not call a model. The agent must cite exact source text on both
    sides; a genuine conflict additionally requires both opposed assertions and same-scope axes.
    """
    if verdict not in VERDICTS - {"insufficient"} or not isinstance(explanation, str) or not explanation.strip():
        return {"status": "evidence_needed", "reason": "invalid_agent_adjudication"}
    for side in (left, right):
        if not isinstance(side.get("id"), str) or not isinstance(side.get("revision"), int) or side["revision"] < 1:
            return {"status": "evidence_needed", "reason": "fragment_identity_missing"}
    if left["id"] == right["id"]:
        return {"status": "evidence_needed", "reason": "same_fragment_identity"}
    try:
        left_evidence = provider.read_fragment(left)
        right_evidence = provider.read_fragment(right)
    except source_evidence.SourceEvidenceError as exc:
        return {"status": "evidence_needed", "reason": exc.code}
    quotes = source_quotes if isinstance(source_quotes, dict) else {}
    for name, evidence in (("left", left_evidence), ("right", right_evidence)):
        selected = quotes.get(name)
        corpus = "\n".join(x.get("quote", "") + "\n" + x.get("context", "") for x in evidence["evidence"])
        if not isinstance(selected, list) or not selected or any(not isinstance(q, str) or q not in corpus for q in selected):
            return {"status": "evidence_needed", "reason": "agent_quote_not_bound_to_source"}
    if verdict == "genuine_conflict":
        if (not isinstance(opposed_assertions, dict) or not isinstance(same_scope, dict)
                or set(same_scope) != {"target", "operation", "condition", "time", "authority"}
                or not all(isinstance(same_scope[k], str) and same_scope[k].strip() for k in same_scope)):
            return {"status": "evidence_needed", "reason": "genuine_conflict_scope_incomplete"}
        for side in ("left", "right"):
            assertion = opposed_assertions.get(side)
            corpus = "\n".join(x.get("quote", "") + "\n" + x.get("context", "")
                                for x in (left_evidence if side == "left" else right_evidence)["evidence"])
            if not isinstance(assertion, str) or not assertion or assertion not in corpus:
                return {"status": "evidence_needed", "reason": "opposed_assertion_not_bound_to_source"}
    try:
        left_after, right_after = provider.read_fragment(left), provider.read_fragment(right)
    except source_evidence.SourceEvidenceError:
        return {"status": "evidence_needed", "reason": "source_changed_during_review"}
    if (left_after["fingerprint"] != left_evidence["fingerprint"]
            or right_after["fingerprint"] != right_evidence["fingerprint"]):
        return {"status": "evidence_needed", "reason": "source_changed_during_review"}
    origin_set = {e["origin"] for e in left_evidence["evidence"] + right_evidence["evidence"]}
    origin = next(iter(origin_set)) if len(origin_set) == 1 else "mixed"
    refs_digest = lambda side: hashlib.sha256(json.dumps(side.get("source", {}).get("evidence_refs", []),
        sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    evidence = {"left": left_evidence["evidence"], "right": right_evidence["evidence"],
                "judgement": {"reviewer": "source-grounded-agent", "verdict": verdict,
                              "explanation": explanation, "supporting_quotes": quotes,
                              "opposed_assertions": opposed_assertions, "same_scope": same_scope}}
    key = hashlib.sha256("\0".join((left["id"], str(left["revision"]), right["id"], str(right["revision"]),
        left_evidence["fingerprint"], right_evidence["fingerprint"], origin, verdict)).encode()).hexdigest()
    return {"status": "reviewed", "origin": origin, "verdict": verdict, "confidence": 1.0,
            "left_id": left["id"], "left_revision": left["revision"], "right_id": right["id"],
            "right_revision": right["revision"], "left_source_fingerprint": left_evidence["fingerprint"],
            "right_source_fingerprint": right_evidence["fingerprint"],
            "left_source_refs_digest": refs_digest(left), "right_source_refs_digest": refs_digest(right),
            "evidence": evidence, "receipt_key": key}


def review_hint_pair(hint_kind, left, right, provider: SourceProvider, judge, *, question_id=None,
                     minimum_confidence=0.8):
    """Qualify one exception/alias hint using current source-bound text and the protected hint contract."""
    if hint_kind not in ("canon_exception", "alias_conflict"):
        return {"status": "evidence_needed", "reason": "unsupported_hint_kind"}
    for side in (left, right):
        if (not isinstance(side, dict) or not isinstance(side.get("id"), str)
                or not isinstance(side.get("revision"), int) or side["revision"] < 1):
            return {"status": "evidence_needed", "reason": "fragment_identity_missing"}
    if left["id"] == right["id"]:
        return {"status": "evidence_needed", "reason": "same_fragment_identity"}
    try:
        left_evidence, right_evidence = provider.read_fragment(left), provider.read_fragment(right)
    except (source_evidence.SourceEvidenceError, OSError) as exc:
        reason = exc.code if isinstance(exc, source_evidence.SourceEvidenceError) else "source_io_error"
        return {"status": "evidence_needed", "reason": reason}
    if hint_kind == "canon_exception":
        choices = ["complementary", "different_target", "historical_or_scope", "genuine_conflict", "insufficient"]
        question = ("Using both current canonical texts and both complete source contexts, decide whether these "
                   "two facts can coexist as complementary/default-and-exception facts, concern different targets, "
                   "differ by source-supported historical time or scope, genuinely conflict on the same target and "
                   "conditions, or lack sufficient evidence. Choose only one typed choice. Do not infer from wording alone.")
    else:
        choices = ["distinct_subject", "same_subject", "insufficient"]
        question = ("Using both current canonical texts and both complete source contexts, decide whether the "
                    "declared alias and the other subject are source-proven distinct subjects, source-proven the same "
                    "subject, or insufficiently established. Shared words are not identity proof. A same-subject result "
                    "must remain pending for the protected human merge workflow; never merge.")
    state = {"hint_kind": hint_kind, "question_id": question_id,
        "left": {"id": left["id"], "revision": left["revision"], "text": left.get("text"),
                 "source_contexts": left_evidence["evidence"]},
        "right": {"id": right["id"], "revision": right["revision"], "text": right.get("text"),
                  "source_contexts": right_evidence["evidence"]}}
    if len(json.dumps(state, ensure_ascii=False)) > 60000:
        return {"status": "evidence_needed", "reason": "context_limit"}
    answers = judge(state, [hint_kind], question, criteria={choice: choice for choice in choices})
    if not isinstance(answers, list) or len(answers) != 1 or not isinstance(answers[0], dict):
        raise ValueError("source hint judge must return one typed choice")
    answer = answers[0]
    outcome = answer.get("choice")
    confidence = _probability(answer, outcome)
    if (outcome not in choices or outcome == "insufficient" or not math.isfinite(confidence)
            or confidence < minimum_confidence):
        return {"status": "evidence_needed", "reason": "semantic_uncertain",
                "hint_kind": hint_kind, "question_id": question_id,
                "left_revision": left["revision"], "right_revision": right["revision"]}
    try:
        left_after, right_after = provider.read_fragment(left), provider.read_fragment(right)
    except (source_evidence.SourceEvidenceError, OSError):
        return {"status": "evidence_needed", "reason": "source_changed_during_review"}
    if (left_after["fingerprint"] != left_evidence["fingerprint"]
            or right_after["fingerprint"] != right_evidence["fingerprint"]):
        return {"status": "evidence_needed", "reason": "source_changed_during_review"}
    # The storage contract uses its existing source-review receipt envelope; the typed hint kind/outcome
    # remains explicit in evidence and maps to the only permitted protected writer outcome.
    if hint_kind == "canon_exception":
        outcome_map = {"complementary": ("compatible", "complementary"),
                       "different_target": ("different_target", "different_target"),
                       "historical_or_scope": ("historical_or_scope", "historical_or_scope")}
    else:
        outcome_map = {"distinct_subject": ("different_target", "distinct_subject"),
                       "same_subject": ("compatible", "same_subject_merge_required")}
    if outcome not in outcome_map:
        return {"status": "evidence_needed", "reason": "human_boundary_or_conflict"}
    verdict, resolved_outcome = outcome_map[outcome]
    origins = {item["origin"] for item in left_evidence["evidence"] + right_evidence["evidence"]}
    origin = next(iter(origins)) if len(origins) == 1 else "mixed"
    refs_digest = lambda side: hashlib.sha256(json.dumps(side.get("source", {}).get("evidence_refs", []),
        sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    judgement = {"reviewer": "typed-jev-source-hint", "kind": hint_kind, "outcome": outcome,
                 "choice": answer, "question_id": question_id}
    receipt_key = hashlib.sha256("\\0".join((left["id"], str(left["revision"]), right["id"],
        str(right["revision"]), left_evidence["fingerprint"], right_evidence["fingerprint"],
        origin, verdict)).encode()).hexdigest()
    return {"status": "reviewed", "origin": origin, "verdict": verdict, "confidence": confidence,
        "hint_kind": hint_kind, "hint_outcome": resolved_outcome,
        "left_id": left["id"], "left_revision": left["revision"],
        "right_id": right["id"], "right_revision": right["revision"],
        "left_source_fingerprint": left_evidence["fingerprint"],
        "right_source_fingerprint": right_evidence["fingerprint"],
        "left_source_refs_digest": refs_digest(left), "right_source_refs_digest": refs_digest(right),
        "evidence": {"left": left_evidence["evidence"], "right": right_evidence["evidence"],
                     "judgement": judgement}, "receipt_key": receipt_key}


def review_pair(left, right, provider: SourceProvider, judge, *, minimum_confidence=0.8):
    """Return a source-origin verdict receipt proposal or evidence-needed; never writes or approves.

    `judge(state, [question], question_text, criteria=...)` is the existing trusted typed-choice
    mechanism. Callers must run it outside protected DB transactions and persist only after CAS.
    """
    for side in (left, right):
        if not isinstance(side.get("id"), str) or not isinstance(side.get("revision"), int) or side["revision"] < 1:
            return {"status": "evidence_needed", "reason": "fragment_identity_missing"}
    if left["id"] == right["id"]:
        return {"status": "evidence_needed", "reason": "same_fragment_identity"}
    try:
        left_evidence = provider.read_fragment(left)
        right_evidence = provider.read_fragment(right)
    except source_evidence.SourceEvidenceError as exc:
        # Stable guard code only; messages may contain locators or environment details.
        return {"status": "evidence_needed", "reason": exc.code}
    origins = {item["origin"] for item in left_evidence["evidence"] + right_evidence["evidence"]}
    origin = (next(iter(origins)) if len(origins) == 1 else "mixed")
    state = {"left": {"id": left["id"], "revision": left["revision"], "text": left.get("text"),
                       "source_contexts": left_evidence["evidence"]},
             "right": {"id": right["id"], "revision": right["revision"], "text": right.get("text"),
                        "source_contexts": right_evidence["evidence"]}}
    if len(json.dumps(state, ensure_ascii=False)) > 60000:
        return {"status": "evidence_needed", "reason": "context_limit"}
    answers = judge(state, ["pair"], SOURCE_REVIEW_QUESTION, criteria=SOURCE_REVIEW_CRITERIA)
    if not isinstance(answers, list) or len(answers) != 1 or not isinstance(answers[0], dict):
        raise ValueError("source review judge must return one typed choice")
    answer = answers[0]
    try:
        left_after = provider.read_fragment(left)
        right_after = provider.read_fragment(right)
    except source_evidence.SourceEvidenceError:
        return {"status": "evidence_needed", "reason": "source_changed_during_review"}
    if (left_after["fingerprint"] != left_evidence["fingerprint"]
            or right_after["fingerprint"] != right_evidence["fingerprint"]):
        return {"status": "evidence_needed", "reason": "source_changed_during_review"}
    verdict = answer.get("choice")
    confidence = _probability(answer, verdict)
    if verdict not in VERDICTS or verdict == "insufficient" or not math.isfinite(confidence) or confidence < minimum_confidence:
        return {"status": "evidence_needed", "reason": "semantic_uncertain", "origin": origin,
                "left_revision": left["revision"], "right_revision": right["revision"],
                "left_source_fingerprint": left_evidence["fingerprint"],
                "right_source_fingerprint": right_evidence["fingerprint"]}
    refs_digest = lambda side: hashlib.sha256(json.dumps(side.get("source", {}).get("evidence_refs", []),
        sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return {"status": "reviewed", "origin": origin, "verdict": verdict, "confidence": confidence,
            "left_id": left["id"], "left_revision": left["revision"],
            "right_id": right["id"], "right_revision": right["revision"],
            "left_source_fingerprint": left_evidence["fingerprint"],
            "right_source_fingerprint": right_evidence["fingerprint"],
            "left_source_refs_digest": refs_digest(left), "right_source_refs_digest": refs_digest(right),
            "evidence": {"left": left_evidence["evidence"], "right": right_evidence["evidence"],
                         "judgement": answer},
            "receipt_key": hashlib.sha256("\0".join((left["id"], str(left["revision"]),
                right["id"], str(right["revision"]), left_evidence["fingerprint"],
                right_evidence["fingerprint"], origin, verdict)).encode()).hexdigest()}
