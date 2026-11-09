"""File-backed experimental ledger; no network or canonical-record writes."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import write_contract as wc
import knowledge_forms


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def event(root, table, row):
    path = Path(root) / table / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(row, ensure_ascii=False) + "\n")


def rows(root, table):
    return [read(p) for p in sorted((Path(root) / table).glob("*.json"))]


def save_validation_receipt(root, receipt):
    # This is the explicitly selected experimental file backend, never a PG fallback.
    import os
    path = Path(root) / 'validation_receipt' / (receipt['receipt_id'] + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as output:
        json.dump(receipt, output, ensure_ascii=False, allow_nan=False)
        output.flush()
        os.fsync(output.fileno())
    return receipt['receipt_id']


def transition(root, entry, state, actor, receipt_ref=None, *, validation_receipt_ref=None):
    old = entry["state"]
    entry["state"] = state
    write(Path(root) / "ledger_entry" / (entry["entry_id"] + ".json"), entry)
    event(root, "ledger_entry", {"entry_id": entry["entry_id"], "from": old, "to": state,
                                 "actor": actor, "receipt_ref": receipt_ref,
                                 'validation_receipt_ref': validation_receipt_ref, "at": now()})


def register(root, judgement):
    import copy
    judgement = copy.deepcopy(judgement)
    errors = wc.admission(judgement, wc.Contract.FILE_LEDGER_V1)
    if not errors:
        try:
            return _register(root, judgement)
        except ValueError as exc:
            errors = [wc.error('E_CONTEXT', 'register', str(exc))]
    receipt = wc.failure_receipt(judgement, errors, stage='register', contract=wc.Contract.FILE_LEDGER_V1)
    save_validation_receipt(root, receipt)
    raise wc.WriteContractError(errors, receipt_id=receipt['receipt_id'], request_id=receipt['request_id'])


def _register(root, judgement):
    root = Path(root)
    unit_id = judgement["work_unit_id"]
    unit_path = root / "work_unit" / (unit_id + ".json")
    if not unit_path.exists():
        unit = {"work_unit_id": unit_id, "host_id": judgement["host_id"],
                "partition_key": judgement["partition_key"], "status": "active",
                "completion_sources": [], "created_at": now(), "completed_at": None}
        write(unit_path, unit)
        event(root, "work_unit", {"work_unit_id": unit_id, "from": None, "to": "active", "source": "register", "at": now()})
    else:
        unit = read(unit_path)
        if (unit["host_id"], unit["partition_key"]) != (judgement["host_id"], judgement["partition_key"]):
            raise ValueError("work unit host/partition mismatch")
    if any(e["judgement_id"] == judgement["judgement_id"] and e["judgement_version"] == judgement["version"]
           for e in rows(root, "ledger_entry")):
        raise ValueError("judgement version already registered")
    entry = {"entry_id": str(uuid4()), "host_id": unit["host_id"], "partition_key": unit["partition_key"],
             "work_unit_id": unit_id, "judgement_id": judgement["judgement_id"],
             "judgement_version": judgement["version"], "judgement_body": judgement,
             "state": "proposed", "supplement_count": 0, "created_at": now()}
    write(root / "ledger_entry" / (entry["entry_id"] + ".json"), entry)
    event(root, "ledger_entry", {"entry_id": entry["entry_id"], "from": None, "to": "proposed",
                                 "actor": "worker", "receipt_ref": None, "at": now()})
    return entry


# the acceptance rule a receipt was made under; a receipt from an older rule is not reused (astra dupA review)
RULE_VERSION = "2026-10-02 add never dropped by a judge alone"


def dedup_key(entry, fact, packet, model, fact_index, stage):
    # schema-delta §3 / event-contract §2: fact 단위 + 검수 단계(worktime/digestion)별 키.
    # packet_digest: 보충(작성 모델 재변환)은 judgement 는 그대로 두고 패킷만 바꾸므로,
    # 패킷 내용이 키에 없으면 고친 패킷이 옛 회부 결과 캐시에 걸린다 (C4 파일럿 발견).
    packet_digest = hashlib.sha256(json.dumps({"state": packet.get("state"), "questions": packet.get("questions")},
                                              sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    source = [entry["judgement_id"], entry["judgement_version"], fact_index,
              hashlib.sha256(json.dumps(fact.get("evidence_refs", entry["judgement_body"].get("evidence_refs", [])),
                                        sort_keys=True).encode()).hexdigest(),
              packet["template_id"], packet["template_version"], model, stage, packet_digest, RULE_VERSION]
    return hashlib.sha256(json.dumps(source, ensure_ascii=False).encode()).hexdigest()


def target(root, fact):
    ref = fact.get("target_ref")
    if not isinstance(ref, str) or not ref or Path(ref).name != ref:
        return None
    path = Path(root) / "fragments" / (ref + ".json")
    return read(path) if path.exists() else None


def build_fragment(entry, fact_index, keywords, receipt_ids, now, seq):
    """Create the 16-field canonical fragment from the unmodified ledger fact."""
    fact = entry["judgement_body"]["facts"][fact_index]
    text = fact["fact"]
    if fact.get("text", text) != text:
        raise ValueError("fragment text differs from ledger fact")
    from runner import lint_fact
    checked = {**fact, "keywords": keywords}
    if not lint_fact(checked)["ok"]:
        raise ValueError("invalid fragment fact or keywords")
    return {"id": str(uuid4()), "host_id": entry["host_id"], "workspace_id": None,
            "alias": f"{entry['partition_key']}-{seq}", "domain": entry["partition_key"],
            "seq": seq, "text": text, "keywords": keywords, "kind": fact.get("kind", "fact"),
            "group_id": f"{entry['judgement_id']}:{fact.get('group', fact_index)}", "revision": 1, "active": True,
            "confidence": "confirmed",
            "source": {"evidence_refs": fact.get("evidence_refs", entry["judgement_body"].get("evidence_refs", [])),
                       "evidence_source": fact.get("evidence_source"), "entry_id": entry["entry_id"],
                       "fact_index": fact_index, "receipt_ids": receipt_ids,
                       "completion_event": entry["work_unit_id"],
                       "captured_by": entry["judgement_body"].get("captured_by", "worker"),
                       "subject": fact.get("subject"), "subject_type": fact.get("subject_type"),
                        "subject_description": fact.get("subject_description"), "parent_subject": fact.get("parent_subject"), "why": fact.get("why"),
                       **knowledge_forms.canonical_source(fact)},
            "valid_from": now, "superseded_at": None}


def batch(root, window, fixtures):
    """Fixtures map entry_id -> list of {packet, answers}; at most window entries per invocation."""
    from runner import lint, decide, lint_fact
    if window < 1:
        raise ValueError("window must be positive")
    root = Path(root)
    selected = [e for e in rows(root, "ledger_entry") if e["state"] == "proposed"][:window]
    result = []
    for entry in selected:
        outcomes, checked_payload = [], []
        body = entry['judgement_body']
        shape_errors = wc.facts_shape(body.get('facts'))
        if shape_errors:
            outcomes.append({'fact_index': None, 'errors': shape_errors})
        for index, fact in enumerate(body.get('facts', []) if isinstance(body.get('facts'), list) else []):
            if not isinstance(fact, dict):
                continue
            errors = wc.fact_shape(fact, wc.Contract.FILE_LEDGER_V1)
            operation = fact.get('operation', 'add')
            fragment = target(root, fact) if not errors and operation in ('update', 'deprecate') else None
            if fragment and fragment.get('host_id') != entry['host_id']:
                fragment = None
            if not errors:
                errors = wc.validate_fact(fact, wc.Contract.FILE_LEDGER_V1)['errors']
            if operation in ("update", "deprecate") and fragment is None:
                errors.append({"code": "E_TARGET", "where": f"facts.{index}.target_ref", "detail": "target missing"})
            fixture = fixtures.get(entry["entry_id"], [])[index] if index < len(fixtures.get(entry["entry_id"], [])) else None
            if fixture is not None:
                errors += wc.validate_fixture(fixture, fact)
                packet = fixture.get('packet', {}) if isinstance(fixture, dict) else {}
                packet_state = packet.get('state') if isinstance(packet, dict) else None
                excerpt = packet_state.get('target_excerpt') if isinstance(packet_state, dict) else None
                if fragment is not None and excerpt != fragment.get("text"):
                    # 코드가 대조 가능한 문자열: target_excerpt 는 대상 조각의 현재 text 그대로여야 함 (C4 발견)
                    errors.append({"code": "E_TARGET", "where": f"facts.{index}.target_excerpt", "detail": "target_excerpt differs from target fragment text"})
            checked_payload.append({'fact_index': index, 'fact': fact, 'fixture': fixture, 'target': fragment})
            if errors:
                outcomes.append({"fact_index": index, "errors": errors})
                continue
            if fixture is None:
                outcomes.append({"fact_index": index, "pending_fixture": True})
                continue
            packet, answers = fixture["packet"], fixture["answers"]
            model = fixture.get("jev_model_actual", "offline-fixture")
            key = dedup_key(entry, fact, packet, model, index, "worktime")
            cached = next((r for r in rows(root, "check_receipt") if r["dedup_key"] == key), None)
            if cached:
                receipt = cached
            else:
                decision = decide(packet, answers, operation=operation)
                receipt = {"receipt_id": str(uuid4()), "entry_id": entry["entry_id"], "fact_index": index,
                           "stage": "worktime", "template_id": packet["template_id"],
                           "template_version": packet["template_version"],
                           "jev_model_requested": fixture.get("jev_model_requested", model),
                           "jev_model_actual": model, "dedup_key": key, "packet": packet,
                           "answers": answers, "combined": decision["combined"],
                           "review_reasons": decision["review_reasons"],
                           "target_fragment_id": fact.get("target_ref") if fragment else None,
                           "target_revision_seen": fragment["revision"] if fragment else None,
                           "created_at": now()}
                write(root / "check_receipt" / (receipt["receipt_id"] + ".json"), receipt)
            outcomes.append({"fact_index": index, "receipt_id": receipt["receipt_id"], "combined": receipt["combined"]})
        if any("errors" in outcome for outcome in outcomes) or not outcomes:
            state = "rejected_input"
        elif any("pending_fixture" in outcome for outcome in outcomes):
            state = "proposed"
        elif any(outcome["combined"] == "needs_review" for outcome in outcomes):
            state = "review_queue"
        elif any(outcome["combined"] in ("record", "update_record", "deprecate_record") for outcome in outcomes):
            state = "provisional"
        else:
            state = "closed"
        validation_id = None
        if state == 'rejected_input':
            reasons = [{**e, 'fact_index': o['fact_index']} for o in outcomes for e in o.get('errors', [])]
            receipt = wc.failure_receipt(body, reasons, stage='worktime', contract=wc.Contract.FILE_LEDGER_V1,
                                         entry=entry, checked=checked_payload)
            validation_id = save_validation_receipt(root, receipt)  # durable reasons before terminal state
        if state != "proposed":
            transition(root, entry, state, "runner", validation_receipt_ref=validation_id)
        result.append({"entry_id": entry["entry_id"], "state": state, "facts": outcomes,
                       'validation_receipt_id': validation_id})
    return result


def resubmit(root, entry_id, actor="luna", limit=2):
    """event-contract §3: review_queue -> 작성자 보충 -> proposed 재진입 (상한 2회, 초과 시 에스컬레이션)."""
    root = Path(root)
    path = root / "ledger_entry" / (entry_id + ".json")
    entry = read(path)
    if entry["state"] != "review_queue":
        raise ValueError("only review_queue entries can be resubmitted")
    if entry["supplement_count"] >= limit:
        entry["escalated"] = True
        write(path, entry)
        event(root, "ledger_entry", {"entry_id": entry_id, "from": "review_queue", "to": "review_queue",
                                     "actor": "runner", "receipt_ref": None, "note": "supplement limit reached -> escalate to human", "at": now()})
        return {"status": "escalated", "supplement_count": entry["supplement_count"]}
    entry["supplement_count"] += 1
    transition(root, entry, "proposed", actor)
    return {"status": "proposed", "supplement_count": entry["supplement_count"]}


def complete(root, unit_id):
    root = Path(root)
    path = root / "work_unit" / (unit_id + ".json")
    unit = read(path)
    if unit["status"] != "active":
        raise ValueError("work unit not active")
    if unit["completion_sources"] and not all(source.get("received") for source in unit["completion_sources"]):
        raise ValueError("completion sources not satisfied")
    unit["status"], unit["completed_at"] = "completed", now()
    write(path, unit)
    event(root, "work_unit", {"work_unit_id": unit_id, "from": "active", "to": "completed",
                              "source": "explicit", "at": now()})
    for entry in rows(root, "ledger_entry"):
        if entry["work_unit_id"] == unit_id and entry["state"] in ("provisional", "review_queue", "closed"):
            transition(root, entry, "eligible", "runner")
    return unit


def digest(root, unit_id, apply=False, fixtures=None):
    """Offline simulation; policy decides acceptance after fresh fixture recheck."""
    root = Path(root)
    unit = read(root / "work_unit" / (unit_id + ".json"))
    if unit["status"] != "completed":
        return {"status": "not_completed"}
    entries = [e for e in rows(root, "ledger_entry") if e["work_unit_id"] == unit_id and e["state"] in ("eligible", "provisional")]
    candidates = []
    for entry in entries:
        for receipt in rows(root, "check_receipt"):
            if receipt["entry_id"] == entry["entry_id"] and receipt["stage"] == "worktime":
                fact = entry["judgement_body"]["facts"][receipt["fact_index"]]
                candidates.append((entry, receipt, fact))
    targets = {}
    for entry, receipt, fact in candidates:
        if fact.get("target_ref"):
            targets.setdefault(fact["target_ref"], []).append(fact["operation"])
    flags = [{"target_ref": ref, "operations": sorted(operations)} for ref, operations in targets.items() if len(operations) > 1]
    stale = [r["receipt_id"] for _, r, f in candidates if r["target_fragment_id"] and
             (target(root, f) is None or target(root, f)["revision"] != r["target_revision_seen"])]
    if stale and not fixtures:
        return {"status": "needs_recheck", "receipt_ids": stale, "consistency_flags": flags}
    if flags:
        return {"status": "needs_review", "consistency_flags": flags}
    if not fixtures:
        return {"status": "needs_recheck", "receipt_ids": [r["receipt_id"] for _, r, _ in candidates],
                "consistency_flags": flags}
    from runner import decide, lint
    from policy import evaluate, apply_utterance_status
    prepared = []
    for entry, old, fact in candidates:
        fixture = fixtures.get(old["receipt_id"])
        if not fixture or not lint(fixture["packet"])["ok"]:
            return {"status": "needs_recheck", "receipt_ids": [old["receipt_id"]], "consistency_flags": flags}
        current = target(root, fact) if fact["operation"] in ("update", "deprecate") else None
        if current and (fixture.get("target_revision_seen") != current["revision"] or
                        fixture["packet"].get("state", {}).get("target_excerpt") != current.get("text")):
            return {"status": "needs_recheck", "receipt_ids": [old["receipt_id"]], "consistency_flags": flags}
        decision = decide(fixture["packet"], fixture["answers"], operation=fact["operation"])
        if decision["combined"] != old["combined"]:
            return {"status": "needs_review", "receipt_ids": [old["receipt_id"]], "consistency_flags": flags}
        evidence_source = fact.get("evidence_source")
        if evidence_source == "user_confirmed" and fixture.get("utterance_status"):
            evidence_source = apply_utterance_status(evidence_source, fixture["utterance_status"])
        verdict = evaluate({"combined": decision["combined"], "review_reasons": decision["review_reasons"],
                            "operation": fact.get("operation", "add"), "kind": fact.get("kind"),
                            "evidence_source": evidence_source, "conflict": False,
                            "can_ask_now": fact.get("can_ask_now", False), "completion": True})
        prepared.append((entry, old, fact, fixture, current, decision, verdict))
    if not apply:
        return {"status": "proposal", "consistency_flags": flags, "count": len(prepared)}
    for _, _, fact, _, current, _, verdict in prepared:
        if verdict["action"] != "absorb":
            continue
        if current and read(root / "fragments" / (fact["target_ref"] + ".json"))["revision"] != current["revision"]:
            return {"status": "cas_rejected", "target_ref": fact["target_ref"]}
    actions = []
    for entry, old, fact, fixture, current, decision, verdict in prepared:
        receipt = {**old, "receipt_id": str(uuid4()), "stage": "digestion", "packet": fixture["packet"],
                   "answers": fixture["answers"], "combined": decision["combined"],
                   "review_reasons": decision["review_reasons"],
                   "target_revision_seen": current["revision"] if current else None, "created_at": now()}
        receipt["dedup_key"] = dedup_key(entry, fact, fixture["packet"], fixture.get("jev_model_actual", "offline-fixture"), old["fact_index"], "digestion")
        write(root / "check_receipt" / (receipt["receipt_id"] + ".json"), receipt)
        ref = fact.get("target_ref")
        action = verdict["action"]
        latest = None
        if action == "absorb" and fact.get("operation", "add") == "add":
            existing = [f for f in rows(root, "fragments") if f.get("host_id") == entry["host_id"] and f.get("domain") == entry["partition_key"]]
            seq = max((f.get("seq", 0) for f in existing), default=0) + 1
            latest = build_fragment(entry, old["fact_index"], fact.get("keywords", []),
                                    [old["receipt_id"], receipt["receipt_id"]], now(), seq)
            ref = latest["id"]
            write(root / "fragments" / (ref + ".json"), latest)
            event(root, "fragment_history", {"fragment_ref": ref, "revision": 1,
                                              "operation": "add", "fragment": latest, "at": now()})
        elif action == "absorb" and fact["operation"] in ("update", "deprecate"):
            path = root / "fragments" / (ref + ".json")
            latest = read(path)
            if latest["revision"] != receipt["target_revision_seen"]:
                return {"status": "cas_rejected", "target_ref": ref}
            latest["revision"] += 1
            if fact["operation"] == "deprecate":
                latest["active"] = False
            else:
                latest["text"] = fact["fact"]
            write(path, latest)
            event(root, "fragment_history", {"fragment_ref": ref, "revision": latest["revision"],
                                             "operation": fact["operation"], "reason": fact["reason"], "at": now()})
        absorption = {"absorption_id": str(uuid4()), "work_unit_id": unit_id,
                      "entry_id": entry["entry_id"], "fact_index": old["fact_index"],
                      "digestion_receipt_id": receipt["receipt_id"],
                      "proposal": {"action": fact["operation"], "target": ref, "text": fact["fact"],
                                   "scope_caveat": None, "review_flags": []},
                      "decision": {"absorb": "absorbed", "retain_as_evidence": "retained_as_evidence",
                                   "reject": "rejected"}.get(action, action),
                      "rule_id": verdict["rule_id"], "action": action, "decided_by": "acceptance_policy",
                      "fragment_ref": ref if action == "absorb" else None,
                      "fragment_revision_after": latest["revision"] if latest else None, "created_at": now()}
        write(root / "absorption" / (absorption["absorption_id"] + ".json"), absorption)
        if action == "queue_for_human":
            write(root / "confirmation_queue" / (absorption["absorption_id"] + ".json"),
                  {"entry_id": entry["entry_id"], "fact_index": old["fact_index"],
                   "absorption_id": absorption["absorption_id"], "rule_id": verdict["rule_id"]})
        transition(root, entry, {"absorb": "absorbed", "retain_as_evidence": "retained_as_evidence",
                                 "reject": "closed"}.get(action, "review_queue"), "acceptance", receipt["receipt_id"])
        actions.append(action)
    return {"status": "absorbed" if actions and all(a == "absorb" for a in actions) else "processed",
            "count": len(prepared)}


def stats(root):
    groups = {}
    for receipt in rows(root, "check_receipt"):
        for name, answer in receipt["answers"].items():
            key = (receipt["template_version"], name)
            group = groups.setdefault(key, {"total": 0, "referred": 0, "flat": 0})
            group["total"] += 1
            if answer["type"] == "choice":
                probabilities = sorted(answer["probabilities"].values(), reverse=True)
                flat = len(probabilities) < 2 or probabilities[0] - probabilities[1] < .15
                escaped = "none_or_uncertain" in str(answer.get("choice", ""))
            else:
                value = float(answer.get("noul", answer.get("value")))
                flat, escaped = .35 <= value <= .65, False
            group["flat"] += int(flat)
            group["referred"] += int(flat or escaped)
    return [{"template_version": version, "question": question, **value}
            for (version, question), value in sorted(groups.items())]
