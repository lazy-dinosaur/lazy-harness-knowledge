"""Offline-first Jev packet linting, routing, and explicit-send transport."""
import argparse
import json
import os
import re
import urllib.request
from pathlib import Path

FORM_OPERATORS = {"EVEN", "IF", "EXCEPT", "WHEN", "THEN", "AND", "OR", "BEFORE", "AFTER", "BECAUSE"}

ESCAPE = re.compile(r"none[_ -]?or[_ -]?uncertain", re.I)
CONDITIONAL = re.compile(r"이면 |라면 |않으면|인 경우.{0,80}고르")
PRESCRIPTIVE = re.compile(r"해야 한다|필요하다|금지한다")
EVALUATIVE = re.compile(r"매우 중요|아주 중요|중요한 발견|핵심|결정적|치명적|사소한|사소해|하찮은")
BACKTICK = re.compile(r"`([^`]+)`")
IDENTIFIER = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:[-/][A-Za-z0-9]+)*\b")
# Unquoted tokens that are clearly code identifiers (write-01: plain acronyms such as API/SSOT and
# capitalized hyphen words such as Hard-stop were false positives). Backticked tokens are always checked.
CODE_TOKEN = re.compile(r"\b(?:[a-z]+[A-Z][A-Za-z0-9]*|[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*|[A-Z0-9]+_[A-Z0-9_]+|"
                        r"[A-Za-z0-9_/.-]+\.(?:tsx|ts|jsx|js|py|sql|md|json|sh|yml|yaml)(?![A-Za-z0-9])|(?:\.?[a-z0-9_-]+/)+[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*|"
                        r"[A-Za-z]+[0-9][A-Za-z0-9.]*)\b")


# Plain acronyms / capitalized words that are vocabulary, not identifiers (write-01 false positives).
PLAIN_ACRONYMS = {"API", "SSOT", "ADR", "SDD", "BDD", "TDD", "DDD", "UI", "UX", "DB", "AI", "LLM", "PR", "CI", "CLI",
                  "URL", "ID", "JSON", "HTML", "CSS", "SQL", "HTTP", "HTTPS", "OK", "MVP", "PG", "RLS", "JWT", "OS"}


def claim_identifiers(claim):
    tokens = {m.group(0) for m in CODE_TOKEN.finditer(claim)}
    # Uppercase-led identifiers with a digit or a hyphen/slash suffix (E5, SCEN-1, RSS/available) stay checked;
    # bare all-caps words are checked unless they are common vocabulary acronyms.
    for token in IDENTIFIER.findall(claim):
        head = token.split("/")[0].split("-")[0]
        if any(c.isdigit() for c in token):
            tokens.add(token)
        elif ("/" in token or "-" in token) and head.isupper() and len(head) > 1 and head not in PLAIN_ACRONYMS:
            tokens.add(token)
        elif token.isupper() and len(token) > 1 and token not in PLAIN_ACRONYMS:
            tokens.add(token)
    return tokens
# 규약 3b/3c 필수 state 키 (template_id 가 있는 패킷에만 적용)
FACT_KINDS = {"fact", "decision", "rationale", "rejected", "constraint", "procedure", "term", "question"}
EVIDENCE_SOURCES = {"user_confirmed", "user_tentative", "official_doc", "code_test", "observed_output", "ai_inference"}
# Fragment storage standard v1 (spec/platform/v2-fragment-knowledge-store.md §1.1) — minimal structural gate
# for worker-authored facts. Only the failure causes observed in cycle-01 are enforced here; meaning is Jev's job.
MULTI_EVIDENCE_KINDS = {"decision", "constraint"}
# Must match knowledge.valid_evidence_refs (migrations/0001): lint rejects what the DB would reject, with the allowed list.
EVIDENCE_REF_TYPES = {"code_test", "official_doc", "user_utterance", "observed_output", "ai_inference"}
QUESTION_TAIL = re.compile(r"(\?|？|그지|맞지|잔아|잖아|아닌가|할까|을까|인가)\s*[.!~]*\s*$")

REQUIRED_STATE = {
    "record-need": {"all": ["narrative", "candidate_fact", "evidence_quote", "existing_records_excerpt"]},
    "intent": {"all": ["narrative", "target_excerpt", "evidence_quote"], "one_of": ["candidate_fact", "deprecate_reason"]},
    "intent-deprecate": {"all": ["narrative", "target_excerpt", "evidence_quote", "deprecate_reason"]},
}

def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def states(packet):
    if "cases" in packet:
        return [(case.get("id", str(i)), case["state"]) for i, case in enumerate(packet["cases"])]
    return [("state", packet.get("state", packet))]


def questions(packet):
    return packet.get("questions", {})


def lint(packet, denylist=()):
    errors = []

    def add(code, where, detail):
        errors.append({"code": code, "where": where, "detail": detail})

    qs = questions(packet)
    if not isinstance(qs, dict):
        # wire 스키마(입력 규칙 6): questions 는 {이름: 질문} 맵. 배열은 크래시가 아니라 거절 (C4 발견)
        add("E_SCHEMA", "questions", "questions must be a map of name -> question")
        qs = {}
    required = REQUIRED_STATE.get(packet.get("template_id")) if isinstance(packet, dict) else None
    if required and isinstance(packet.get("state"), dict):
        state = packet["state"]
        for key in required["all"]:
            if key not in state:
                add("E_STATE", f"state.{key}", "required state key missing")
        if required.get("one_of") and not any(k in state for k in required["one_of"]):
            add("E_STATE", "state", "one of " + "/".join(required["one_of"]) + " required")
    for name, question in qs.items():
        where = f"questions.{name}"
        if not isinstance(question, dict):
            add("E_SCHEMA", where, "question must be an object")
            continue
        if set(question) != {"type", "instructions", "criteria"} or question.get("type") not in ("choice", "noul"):
            add("E_SCHEMA", where, "expected type, instructions, criteria and choice|noul")
        kind, criteria = question.get("type"), question.get("criteria")
        if kind == "choice":
            if not isinstance(criteria, dict):
                add("E_SCHEMA", where + ".criteria", "choice criteria must be a map")
            elif not any(ESCAPE.search(key) for key in criteria):
                add("E_ESCAPE", where + ".criteria", "missing none_or_uncertain escape")
        elif kind == "noul" and (not isinstance(criteria, dict) or set(criteria) != {"true", "false"}):
            add("E_SCHEMA", where + ".criteria", "noul criteria must have true and false only")
        if isinstance(question.get("instructions"), str) and CONDITIONAL.search(question["instructions"]):
            add("E_CONDITIONAL", where + ".instructions", "conditional instruction")

    for case_id, state in states(packet):
        if not isinstance(state, dict):
            continue
        where = f"{case_id}.state"
        if PRESCRIPTIVE.search(str(state.get("narrative", ""))):
            add("E_PRESCRIPTIVE", where + ".narrative", "prescriptive narrative")
        if EVALUATIVE.search(str(state.get("narrative", ""))):
            add("E_EVALUATIVE", where + ".narrative", "evaluative narrative")
        quote = str(state.get("evidence_quote", ""))
        claim = str(state.get("candidate_fact", state.get("statement", "")))
        # IF/THEN/AND/OR… are fact_form structure, not content: a split fact keeps the quoted words, not the operators
        # (2026-10-01 main canon split was rejected with 'IF absent from evidence_quote').
        tokens = (set(BACKTICK.findall(claim)) | claim_identifiers(claim)) - FORM_OPERATORS
        for token in sorted(tokens):
            if token not in quote:
                add("E_CLAIM_QUOTE", where + ".candidate_fact", f"{token} absent from evidence_quote")
    serialized = json.dumps(packet, ensure_ascii=False)
    for secret in denylist:
        if secret and secret in serialized:
            add("E_LEAK", "packet", "denylisted string present")
    return {"ok": not errors, "errors": errors}


SUBJECT_HEAD = re.compile(r"^(.{2,60}?)(?:은|는|이|가|에서는|에서|의)\s")
from subject_contract import SUBJECT_TYPES, metadata_errors, code_candidates, subject_in_fact

SUBJECT_MAX_CHARS = 60
SUBJECT_OPERATORS = re.compile(r"\b(?:IF|THEN|AND|OR|EVEN|EXCEPT|WHEN|BEFORE|AFTER|BECAUSE)\b", re.I)
SUBJECT_ENDINGS = re.compile(r"(?:하거나|하면|한다|된다|이다|있다|없다|하며|이고|거나|지만|으므로|하므로|때문에)(?:\s|[.!?…]|$)")


def subject_error(subject):
    """Syntactic guard, not a semantic object classifier; the author owns object identity."""
    if not isinstance(subject, str) or not subject.strip():
        return "subject must name one real domain object"
    if (len(subject) > SUBJECT_MAX_CHARS or SUBJECT_OPERATORS.search(subject)
            or SUBJECT_ENDINGS.search(subject) or re.search(r"[\n\r!?…]", subject)):
        return "subject must be an object noun phrase <=60 chars, not a sentence/operator/verb ending"
    if (subject.strip() in {"저장", "닫기", "삭제", "같은 장치"} or subject.strip().endswith(("버튼", "button", "테스트 항목", "테스트 케이스", "테스트", "조건", "값", "동작", "액션", "클릭", "저장", "삭제", "닫기", "열기"))
            or subject.strip().lower().endswith(("button", "test case", ".test", ".spec"))
            or re.match(r"^(?:test_|it\()", subject.strip()) or re.fullmatch(r"(?:true|false|null|\d+(?:\.\d+)?)", subject.strip(), re.I)):
        return "name the owning feature/screen/component, not its button/action/value"
    return None

KEYWORD_TOKEN = re.compile(r"`([^`]{2,60})`|\b([A-Za-z][A-Za-z0-9_.-]{2,40})\b")


def normalize_fact(fact):
    """Repair form-only issues before lint (write-01: form rejects dropped 60% of well-written facts).
    Never touches meaning: fact text, quotes, evidence and source stay as written. Returns (fact, repairs)."""
    fact, repairs = dict(fact), []
    text = str(fact.get("fact", ""))
    subject = fact.get("subject")
    # Object identity is semantic, not a form repair. Missing/non-verbatim subjects stay lint errors.
    quote = str(fact.get("evidence_quote") or " / ".join(
        r.get("quote", "") for r in fact.get("evidence_refs", []) if isinstance(r, dict)))
    kept = [k for k in fact.get("keywords", []) if isinstance(k, str) and k and (k in text or k in quote)]
    if len(kept) != len(fact.get("keywords", []) or []) or not kept:
        auto = []
        for m in KEYWORD_TOKEN.finditer(text):
            token = (m.group(1) or m.group(2)).strip()
            if token and token not in auto and token not in kept:
                auto.append(token)
        new_kw = (kept + auto)[:6]
        if new_kw != fact.get("keywords"):
            repairs.append({"field": "keywords", "from": fact.get("keywords"), "to": new_kw})
        fact["keywords"] = new_kw
    return fact, repairs


# Codes that stay blocking (meaning/trust). Form-only codes are repaired or reported as warnings.
BLOCKING_CODES = {"E_KIND", "E_SOURCE", "E_GROUP", "E_SUBJECT", "E_SUBJECT_OBJECT", "E_SUBJECT_TYPE", "E_SUBJECT_DESCRIPTION", "E_WHY", "E_USER_REF", "E_KEYWORD", "E_REF"}
WARNING_CODES = {"E_EVIDENCE_COUNT"}


def lint_fact(fact, strict_refs=False):
    """Validate judgement facts separately from Jev wire packets.
    'errors' block registration; 'warnings' are reported only (Jev is_supported judges evidence sufficiency)."""
    errors = []
    if "kind" in fact and fact["kind"] not in FACT_KINDS:
        errors.append({"code": "E_KIND", "where": "kind", "detail": "unknown fact kind; allowed: " + ", ".join(sorted(FACT_KINDS))})
    if "evidence_source" in fact and fact["evidence_source"] not in EVIDENCE_SOURCES:
        errors.append({"code": "E_SOURCE", "where": "evidence_source",
                       "detail": "unknown evidence source; allowed: " + ", ".join(sorted(EVIDENCE_SOURCES))})
    # strict_refs: the DB-backed path (knowledge.valid_evidence_refs); the legacy file store keeps plain-string refs.
    raw_refs = fact.get("evidence_refs", []) if strict_refs else []
    if strict_refs and not isinstance(raw_refs, list):
        errors.append({"code": "E_REF", "where": "evidence_refs", "detail": "evidence_refs must be a list"})
        raw_refs = []
    for index, ref in enumerate(raw_refs):
        where = f"evidence_refs.{index}"
        if not isinstance(ref, dict):
            errors.append({"code": "E_REF", "where": where, "detail": "each evidence ref must be an object {type, locator, quote}"})
            continue
        if ref.get("type") not in EVIDENCE_REF_TYPES:
            errors.append({"code": "E_REF", "where": where + ".type",
                           "detail": f"unknown evidence ref type {ref.get('type')!r}; allowed: " + ", ".join(sorted(EVIDENCE_REF_TYPES))
                                     + " (code/test lines -> code_test, your own earlier answer -> observed_output or ai_inference)"})
        if not isinstance(ref.get("locator"), str) or not ref["locator"].strip():
            errors.append({"code": "E_REF", "where": where + ".locator", "detail": "locator must be a nonempty string"})
        if not isinstance(ref.get("quote"), str):
            errors.append({"code": "E_REF", "where": where + ".quote", "detail": "quote must be a string"})
    if "group" in fact and (not isinstance(fact["group"], str) or not fact["group"].strip()):
        errors.append({"code": "E_GROUP", "where": "group", "detail": "group must be a nonempty string"})
    subject = fact.get("subject")
    if not isinstance(subject, str) or not subject.strip():
        errors.append({"code": "E_SUBJECT", "where": "subject", "detail": "subject (what the fact is about) is required"})
    elif not subject_in_fact(fact):
        errors.append({"code": "E_SUBJECT", "where": "subject", "detail": "subject or one of its aliases must appear verbatim in fact"})
    if isinstance(subject, str) and subject_error(subject):
        errors.append({"code": "E_SUBJECT_OBJECT", "where": "subject", "detail": subject_error(subject)})
    errors.extend(metadata_errors(fact))
    from knowledge_forms import errors as form_errors
    errors.extend(form_errors(fact))
    refs = [r for r in fact.get("evidence_refs", []) if isinstance(r, dict)]
    if fact.get("kind") in MULTI_EVIDENCE_KINDS and len({(r.get("type"), r.get("locator")) for r in refs}) < 2:
        errors.append({"code": "E_EVIDENCE_COUNT", "where": "evidence_refs",
                       "detail": "decision/constraint needs >=2 distinct evidence refs (who decided + what was decided)"})
    if fact.get("kind") == "decision" and (not isinstance(fact.get("why"), str) or not fact["why"].strip()):
        errors.append({"code": "E_WHY", "where": "why", "detail": "decision needs why (the stated reason)"})
    if fact.get("evidence_source") == "user_confirmed":
        # Standard v1 §1.1 criterion 7 (revision 3, 2026-09-27): EVERY cited user utterance must be a non-question.
        # A question cited next to a confirmation used to slip through because one non-question was enough.
        utterances = [r for r in refs if r.get("type") == "user_utterance"]
        usable = [r for r in utterances if isinstance(r.get("quote"), str) and r["quote"].strip()]
        questions = [r for r in usable if QUESTION_TAIL.search(r["quote"].strip())]
        if not usable:
            errors.append({"code": "E_USER_REF", "where": "evidence_refs",
                           "detail": "user_confirmed needs a verbatim non-question user_utterance quote; ask the user to confirm first"})
        elif questions or len(usable) != len(utterances):
            errors.append({"code": "E_USER_REF", "where": "evidence_refs",
                           "detail": "user_confirmed: every cited user_utterance must be a non-question confirmation; "
                                     "cite a question as context with another evidence type, or ask the user to confirm"})
    for index, keyword in enumerate(fact.get("keywords", [])):
        if not isinstance(keyword, str) or not keyword or not any(
                keyword in text for text in (fact.get("fact"), fact.get("evidence_quote")) if isinstance(text, str)):
            errors.append({"code": "E_KEYWORD", "where": f"keywords.{index}", "detail": "keyword absent from fact and evidence_quote"})
    warnings = [e for e in errors if e["code"] in WARNING_CODES]
    errors = [e for e in errors if e["code"] not in WARNING_CODES]
    return {"ok": not errors, "errors": errors, "warnings": warnings}


# Digestion drops an add as duplicate only when Jev is nearly certain it is not new (write-01 round_dedup:
# at <0.5 digestion dropped 22% of genuinely new facts that added one detail to an existing fragment; at <0.1
# new-fact loss is 0.5%). Remaining near-duplicates are left for a separate, later clean-up job.
DUP_SKIP_MAX = 0.1


def decide(packet, responses, operation="add"):
    per_question, reasons = {}, []
    for key, response in responses.items():
        kind = response["type"]
        if kind == "choice":
            probabilities = response["probabilities"]
            ranked = sorted(probabilities, key=probabilities.get, reverse=True)
            choice = response.get("choice", response.get("value", ranked[0]))
            review = len(ranked) < 2 or probabilities[ranked[0]] - probabilities[ranked[1]] < 0.15 or bool(ESCAPE.search(str(choice)))
            value = choice
        elif kind == "noul":
            value = float(response.get("noul", response.get("value")))
            review = 0.35 <= value <= 0.65
        else:
            raise ValueError(f"unsupported response type: {kind}")
        route = "route_review" if review else "auto"
        per_question[key] = {"route": route, "value": value}
        if review:
            reasons.append(f"{key}: route_review")
    # 범위 보존 가드 (C4 발견): scope 가 확실히 expanded 면 근거보다 넓은 주장 — 기록 통과 금지, 회부.
    # (narrowed 는 보수적 과소 주장이라 막지 않는다.) duplicate/no_record 판정은 그대로 우선한다.
    if "scope" in per_question and per_question["scope"]["route"] == "auto" and per_question["scope"]["value"] == "expanded":
        reasons.append("scope: expanded (claim broader than evidence)")
    if operation in ("update", "deprecate"):
        required = ({"differs_from_target", "correction_evidence"} if operation == "update" else
                    {"invalidation_evidence", "replacement_exists"})
        if not required <= per_question.keys():
            combined = "needs_review"
            reasons.append("missing v0.3 response")
        elif operation == "update" and per_question["differs_from_target"]["route"] == "auto" and per_question["differs_from_target"]["value"] < .5:
            combined = "duplicate_skip"
        elif reasons:
            combined = "needs_review"
        elif operation == "update" and per_question["differs_from_target"]["value"] >= .5 and per_question["correction_evidence"]["value"] == "refutes":
            combined = "update_record"
        elif operation == "deprecate" and per_question["invalidation_evidence"]["value"] == "invalidates" and per_question["replacement_exists"]["value"] in ("replacement_provided", "no_replacement_needed"):
            combined = "deprecate_record"
        else:
            combined = "needs_review"
            reasons.append("v0.3 combination unresolved")
        return {"per_question": per_question, "combined": combined, "review_reasons": reasons}
    if "impact" in per_question:
        if not {"is_new", "durability", "impact"} <= per_question.keys():
            combined = "needs_review"
            reasons.append("missing v0.2 response")
        else:
            is_new = per_question["is_new"]
            durability = per_question["durability"]
            impact = per_question["impact"]
            if is_new["route"] == "auto" and is_new["value"] < DUP_SKIP_MAX:
                combined = "duplicate_skip"
            elif durability["route"] == "auto" and durability["value"] in ("transient_progress", "explanation_only"):
                combined = "no_record"
            elif impact["route"] == "auto" and impact["value"] == "no_future_use":
                combined = "no_record"
            elif reasons:
                combined = "needs_review"
            elif impact["route"] == "auto" and impact["value"] == "reference_only":
                combined = "needs_review"
                reasons.append("impact: reference_only (policy pending)")
            elif is_new["value"] >= 0.5 and durability["value"] == "durable_fact" and impact["value"] == "changes_decisions":
                combined = "record"
            else:
                combined = "needs_review"
                reasons.append("v0.2.2 combination unresolved")
    elif not {"is_new", "durability", "should_record"} <= per_question.keys():
        combined = "needs_review"
        reasons.append("missing v0.2 response")
    else:
        is_new = per_question["is_new"]
        durability = per_question["durability"]
        should = per_question["should_record"]
        # 결합 우선순위 (Parent 설계 확정 2026-09-24):
        # 1) 확실한 중복(is_new auto-no) → duplicate_skip 이 다른 축 회부보다 우선
        # 2) 확실한 transient/explanation(durability auto) → no_record 가 경계 noul 회부보다 우선
        # 3) 남은 회부 신호 → needs_review
        # 4) 전부 확실할 때만 record
        if is_new["route"] == "auto" and is_new["value"] < DUP_SKIP_MAX:
            combined = "duplicate_skip"
        elif durability["route"] == "auto" and durability["value"] in ("transient_progress", "explanation_only"):
            combined = "no_record"
        elif reasons:
            combined = "needs_review"
        elif durability["value"] == "durable_fact" and is_new["value"] >= 0.5 and should["value"] >= 0.5:
            combined = "record"
        else:
            combined = "needs_review"
            reasons.append("v0.2 combination unresolved")
    return {"per_question": per_question, "combined": combined, "review_reasons": reasons}


def build_request(packet, case_id):
    matches = [state for name, state in states(packet) if name == case_id]
    if len(matches) != 1:
        raise ValueError(f"unknown or ambiguous case: {case_id}")
    if not questions(packet):
        raise ValueError("call requires explicit questions; plan evidence has no wire questions")
    model = packet.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model is required for call")
    return {"state": matches[0], "questions": questions(packet), "model": model}


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    lint_cmd = commands.add_parser("lint")
    lint_cmd.add_argument("packet")
    lint_cmd.add_argument("--denylist")
    decision = commands.add_parser("decide")
    decision.add_argument("packet")
    decision.add_argument("responses")
    decision.add_argument("--operation", choices=("add", "update", "deprecate"), default="add")
    call = commands.add_parser("call")
    call.add_argument("packet")
    call.add_argument("case_id")
    call.add_argument("--send", action="store_true")
    register_cmd = commands.add_parser("register")
    register_cmd.add_argument("judgement")
    batch_cmd = commands.add_parser("batch")
    batch_cmd.add_argument("--window", type=int, required=True)
    batch_cmd.add_argument("--responses", required=True, help="offline fixture map by entry_id")
    digest_cmd = commands.add_parser("digest")
    digest_cmd.add_argument("work_unit_id")
    digest_cmd.add_argument("--apply", action="store_true")
    digest_cmd.add_argument("--responses", help="offline fresh digestion fixture map by worktime receipt_id")
    complete_cmd = commands.add_parser("complete")
    complete_cmd.add_argument("work_unit_id")
    resubmit_cmd = commands.add_parser("resubmit")
    resubmit_cmd.add_argument("entry_id")
    commands.add_parser("stats")
    search_cmd = commands.add_parser("search")
    search_cmd.add_argument("--host", required=True)
    search_cmd.add_argument("--query", required=True)
    search_cmd.add_argument("--limit", type=int, default=8)
    search_cmd.add_argument("--cross-hosts", help="comma-separated host IDs")
    search_cmd.add_argument("--mode", choices=("text", "hybrid"), default="text")
    search_cmd.add_argument("--min-similarity", type=float)
    collect_cmd = commands.add_parser("collect", help="collect topic with oracle or Jev judge")
    collect_cmd.add_argument("--host", required=True)
    collect_cmd.add_argument("--queries-file", required=True, help="UTF-8 text, one query per line")
    collect_cmd.add_argument("--judge", choices=("oracle", "jev"), default="oracle")
    collect_cmd.add_argument("--oracle", help="JSON array of correct fragment UUIDs (oracle judge)")
    collect_cmd.add_argument("--topic-title", help="topic name for Jev (defaults to host)")
    collect_cmd.add_argument("--question", choices=("relaxed", "narrow"), default="relaxed")
    collect_cmd.add_argument("--threshold", type=float, default=0.5)
    collect_cmd.add_argument("--variant", choices=("plain", "ctx-v1", "ctx-v2"), default="ctx-v2")
    collect_cmd.add_argument("--page-size", type=int, default=30)
    commands.add_parser("embed-server", help="run loopback embedding service in foreground (model venv required)")
    backfill_cmd = commands.add_parser("backfill-embeddings")
    backfill_cmd.add_argument("--host", required=True)
    backfill_cmd.add_argument("--variant", choices=("plain", "ctx-v1", "ctx-v2"), default="ctx-v2")
    for command in (register_cmd, batch_cmd, digest_cmd, complete_cmd, resubmit_cmd, commands.choices["stats"], search_cmd, backfill_cmd, collect_cmd):
        command.add_argument("--ledger", default=str(Path(__file__).parent / "ledger"))
        command.add_argument("--backend", choices=("file", "pg"), default="file")
        command.add_argument("--dsn", help="PostgreSQL DSN (alternatively LH_KNOWLEDGE_TEST_DSN)")
    args = parser.parse_args()
    if args.command == "embed-server":
        import embed_server
        embed_server.serve(int(os.environ.get("LH_EMBED_PORT", embed_server.DEFAULT_PORT)))
        return
    if args.command in ("register", "batch", "digest", "complete", "resubmit", "stats", "search", "backfill-embeddings", "collect"):
        if args.command in ("search", "backfill-embeddings", "collect") and args.backend != "pg":
            parser.error("search requires --backend pg; file backend does not support search")
        if args.backend == "pg":
            import store_pg as store
            location = args.dsn or os.environ.get("LH_KNOWLEDGE_TEST_DSN")
            if not location:
                parser.error("pg backend requires --dsn or LH_KNOWLEDGE_TEST_DSN")
        else:
            import store
            location = args.ledger
        if args.command == "collect":
            from collect import collect_topic
            queries = [line.strip() for line in Path(args.queries_file).read_text(encoding="utf-8").splitlines() if line.strip()]
            if args.judge == "oracle":
                if not args.oracle:
                    parser.error("oracle judge requires --oracle")
                gold = set(load(args.oracle))
                judge = lambda q, cs: [{"relevant": c["id"] in gold, "novel": c["id"] in gold} for c in cs]
            else:
                if args.oracle:
                    parser.error("--oracle is only for oracle judge")
                import config
                try:
                    cfg = config.require("jev_api_key", "jev_base_url", "jev_model")
                except config.ConfigError as exc:
                    parser.error(str(exc))
                from judge_jev import make_jev_judge
                judge = make_jev_judge(args.topic_title or args.host, queries, base_url=cfg["jev_base_url"],
                                       api_key=cfg["jev_api_key"], model=cfg["jev_model"],
                                       question=args.question, threshold=args.threshold)
            output = collect_topic(location, args.host, queries, page_size=args.page_size,
                                   variant=args.variant, judge=judge)
            if args.judge == "jev":
                output["judgments"] = judge.judgments
        elif args.command == "backfill-embeddings":
            output = store.backfill_embeddings(location, args.host, args.variant)
        elif args.command == "search":
            results = store.search(location, args.host, args.query, args.limit,
                                   args.cross_hosts.split(",") if args.cross_hosts else None, mode=args.mode,
                                   min_similarity=args.min_similarity)
            output = [{key: result[key] for key in ("alias", "kind", "text", "evidence_quote", "host_id", "score", "vector_rank", "text_rank", "vector_similarity", "mode_used", "warning") if key in result}
                      | {"group": [{key: sibling[key] for key in ("alias", "kind", "text", "evidence_quote", "host_id")}
                                   for sibling in result["group"]]} for result in results]
        elif args.command == "register":
            output = store.register(location, load(args.judgement))
        elif args.command == "batch":
            output = store.batch(location, args.window, load(args.responses))
        elif args.command == "digest":
            output = store.digest(location, args.work_unit_id, args.apply,
                                  load(args.responses) if args.responses else None)
        elif args.command == "complete":
            output = store.complete(location, args.work_unit_id)
        elif args.command == "resubmit":
            output = store.resubmit(location, args.entry_id)
        else:
            output = store.stats(location)
        print(json.dumps(output, ensure_ascii=False, default=str))
        return
    packet = load(args.packet)
    if args.command == "lint":
        denylist = Path(args.denylist).read_text(encoding="utf-8").splitlines() if args.denylist else []
        output = lint(packet, denylist)
    elif args.command == "decide":
        output = decide(packet, load(args.responses), operation=args.operation)
    else:
        output = build_request(packet, args.case_id)
        if args.send:
            import config
            cfg = config.require("jev_api_key", "jev_base_url")
            key, base = cfg["jev_api_key"], cfg["jev_base_url"]
            request = urllib.request.Request(base.rstrip("/") + "/v1/systemone",
                data=json.dumps(output).encode("utf-8"),
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
            import host_defaults
            with host_defaults.judgement_http()(request, timeout=30) as reply:
                output = json.load(reply)
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
