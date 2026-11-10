"""Single-session worker entry point. Review and absorption remain the poller's responsibility."""
import json
import os
import re
import subprocess
import sys
from uuid import UUID, uuid4

import config
import runner
import store_pg
import worktime_driver
import write_contract as wc
import validation_receipts as vr


def required(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value


def unit_id(value):
    try:
        return str(UUID(required(value, "work_unit_id")))
    except ValueError:
        raise ValueError("invalid work_unit_id") from None


def baseline_ref(cwd):
    if not isinstance(cwd, str) or not cwd:
        return None
    try:
        result = subprocess.run(["git", "-C", cwd, "rev-parse", "--verify", "HEAD"],
                                capture_output=True, text=True, timeout=5)
        return result.stdout.strip() if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", result.stdout.strip()) else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def snapshot(dsn, uid):
    with store_pg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""select status::text,host_id,partition_key,baseline_history_id,baseline_code_ref,baseline_at,
                   completion_sources from knowledge.work_unit where work_unit_id=%s""", (uid,))
        unit = store_pg._row(cur)
        if unit is None:
            raise ValueError("unknown work_unit_id")
        cur.execute("select state::text,count(*) from knowledge.ledger_entry where work_unit_id=%s group by state", (uid,))
        states = dict(cur.fetchall())
        cur.execute("""select distinct f.alias from knowledge.fragment f join knowledge.absorption a on a.fragment_ref=f.id
                   join knowledge.ledger_entry e on e.entry_id=a.entry_id where e.work_unit_id=%s order by f.alias""", (uid,))
        aliases = [row[0] for row in cur.fetchall()]
        cur.execute('select entry_id::text,judgement_body from knowledge.ledger_entry where work_unit_id=%s order by created_at', (uid,))
        proposals = [{'entry_id': eid, 'domain_proposal': body['partition_key'],
                      'subjects': [{'fact_index': i, **f['subject_provisional']} for i, f in enumerate(body['facts']) if f.get('subject_provisional')]}
                     for eid, body in cur.fetchall()]
    baseline = {k: unit[k] for k in ("baseline_history_id", "baseline_code_ref", "baseline_at")}
    return {"work_unit_id": uid, "status": unit["status"], "host_id": unit["host_id"],
            "partition_key": unit["partition_key"], "baseline": baseline, 'record_proposals': proposals,
            "entry_states": {k: states.get(k, 0) for k in ("proposed", "provisional", "review_queue", "rejected_input")},
            "all_entry_states": states, "absorbed_aliases": aliases,
            "changed_since_baseline": len(store_pg.changed_since_baseline(dsn, uid))}


def lint_update_fact(dsn, host, raw):
    """The record path's per-fact checks for one update fact, without registering. Returns error list."""
    fact = dict(raw)
    fact["evidence_quote"] = " / ".join(r["quote"] for r in fact.get("evidence_refs", []) if isinstance(r, dict) and isinstance(r.get("quote"), str))
    fact, _ = runner.normalize_fact(fact)
    errors = list(runner.lint_fact(fact, strict_refs=True)["errors"])
    try:
        packet = worktime_driver.build_packet(fact, worktime_driver._target_excerpt(dsn, host, fact))
        errors += runner.lint(packet)["errors"]
    except (KeyError, TypeError, ValueError) as exc:
        errors.append({"code": "E_PACKET", "detail": type(exc).__name__})
    return errors


def record(dsn, data, *, same=None, embed_fn=None, assigned_area=None, isolate_invalid=False, source_text="", grounding_check=None, fixed_subject=False, leaf_references=True, trusted_identity_refs=None):
    # Public/tool and trusted fixed-identity producers share this boundary. Deep copy
    # BEFORE normalization: code evidence enrichment must not mutate caller refs.
    import copy
    original = copy.deepcopy(data)
    clean, errors = wc.public_input(data, config.load().get('default_host'))
    shape_errors = None
    if errors and isolate_invalid and isinstance(clean, dict) and isinstance(clean.get('facts'), list):
        shape_errors = wc.fact_error_groups(errors, len(clean['facts']))
        if shape_errors is not None:
            errors = []  # only fact-scoped failures; envelope failures still reject all
    try:
        result = ({'ok': False, 'errors': errors} if errors else
                  _record(dsn, clean, same=same, embed_fn=embed_fn, assigned_area=assigned_area,
                          isolate_invalid=isolate_invalid, source_text=source_text, grounding_check=grounding_check,
                          fixed_subject=fixed_subject, leaf_references=leaf_references, trusted_identity_refs=trusted_identity_refs, shape_errors=shape_errors))
    except wc.WriteContractError:
        raise  # already durably recorded, or explicit migration/persistence failure
    except ValueError as exc:
        result = {'ok': False, 'errors': [wc.error('E_CONTEXT', 'record', str(exc))]}
    checked = result.pop('_checked_facts', None)
    entry = result.pop('_validation_entry', None)
    reasons = list(result.get('errors', []))
    reasons += [{**e, 'fact_index': item['fact_index']} for item in result.get('held_facts', []) for e in item['errors']]
    if reasons:
        receipt = wc.failure_receipt(original, reasons, stage='record', contract=wc.Contract.PUBLIC_V1,
                                     entry=entry, checked=checked)
        try:
            vr.save(dsn, receipt)  # never report a durable failure if persistence failed
        except Exception as exc:
            if entry is None:
                raise
            registration = dict(entry_id=entry['entry_id'], work_unit_id=result['work_unit_id'],
                                state=result['state'], registration_committed=True,
                                recorded_indices=result['recorded_indices'], held_facts=result['held_facts'],
                                resubmit_record=False)
            failure_errors = (exc.errors if isinstance(exc, wc.WriteContractError) else
                              [wc.error('E_RECEIPT_PERSISTENCE', 'validation_receipt', type(exc).__name__)])
            raise wc.WriteContractError(failure_errors, request_id=receipt['request_id'],
                                        registration=registration) from exc
        result.update(validation_receipt_id=receipt['receipt_id'], request_id=receipt['request_id'])
    return result


def _record(dsn, data, *, same=None, embed_fn=None, assigned_area=None, isolate_invalid=False, source_text="", grounding_check=None, fixed_subject=False, leaf_references=True, trusted_identity_refs=None, shape_errors=None):
    host = data.get("host_id") or config.load()["default_host"]
    required(host, "host_id (set default_host in knowledge.json)")
    partition = required(data.get("partition_key"), "partition_key")
    facts = data.get("facts")
    if not isinstance(facts, list) or not facts:
        return {"ok": False, "errors": [{"code": "E_FACTS", "where": "facts", "detail": "nonempty facts array required"}]}
    # Only the server call option can authorize a ref. Public JSON IDs are dropped.
    from identity_ref import checked as checked_ref, resolve as resolve_ref, IdentityError
    if trusted_identity_refs is not None:
        try:
            if not fixed_subject or leaf_references or not assigned_area or len(trusted_identity_refs) != len(facts):
                raise IdentityError('E_IDENTITY: trusted refs require fixed own-leaf area path')
            trusted_identity_refs = [checked_ref(ref,host,assigned_area) for ref in trusted_identity_refs]
        except (ValueError,TypeError) as exc:
            return {'ok':False,'errors':[{'code':'E_IDENTITY','where':'facts','detail':str(exc)}]}
    errors = [e for group in (shape_errors or {}).values() for e in group]
    warnings, repairs = [], []
    normalized, checked_facts = [], []
    normalized_indices = []
    # subjects (schema-delta '주어는 기록할 때 맞추고 소화할 때 등록'): the dictionary is read here, never written
    import subject_dict
    if same is None and data.get("resolve_subjects"):
        cfg_ = config.load()
        if all(cfg_.get(k) for k in ("jev_api_key", "jev_base_url", "jev_model")):
            import worker_tools
            same = worker_tools.make_ask(cfg_)
    import embed
    if same is not None and (embed_fn is None or embed_fn is embed.encode_queries):
        from embedding_inputs import warm
        try:
            warm_facts = [f for i, f in enumerate(facts) if i not in (shape_errors or {})]
            if warm_facts:
                warm(warm_facts, partition)
        except embed.EmbeddingUnavailable:
            pass  # preserve existing text-fallback behavior
    resolve = None if trusted_identity_refs is not None else subject_dict.resolver(store_pg.connect, dsn, host,
                                    unit_id(data["work_unit_id"]) if data.get("work_unit_id") else None, same, embed_fn,
                                    **({"domain":assigned_area} if assigned_area else {}))
    subjects, provisional_subjects = [], []
    added = None  # git diff lines of the working tree, read once when an update needs code evidence
    for index, raw in enumerate(facts):
        if index in (shape_errors or {}):
            checked_facts.append({'fact_index': index, 'fact': raw})
            continue  # held by the indexed shape errors, before normalization/warmup
        if not isinstance(raw, dict):
            errors.append({"code": "E_SCHEMA", "where": f"facts.{index}", "detail": "fact must be an object"})
            continue
        fact = dict(raw)
        for key in ('identity_ref','subject_id','host_id'):
            fact.pop(key,None)  # untrusted worker/model JSON can never authorize IDs
        if trusted_identity_refs is not None:
            ref = trusted_identity_refs[index]
            try:
                with store_pg.connect(dsn) as conn, conn.cursor() as cur:
                    obj = resolve_ref(cur,ref,host,assigned_area)
                if obj['subject_id'] == ref['subject_id'] and fact.get('subject') != obj['name']:
                    raise IdentityError('E_IDENTITY: selected display name changed')
            except IdentityError as exc:
                errors.append({'code':'E_IDENTITY','where':f'facts.{index}.subject','detail':str(exc)})
                continue
            fact.update(identity_ref=ref,subject_id=obj['subject_id'])
        fact.pop("assigned_area", None)  # trusted router only, never worker input
        fact["domain_is_hint"] = not bool(assigned_area)
        if assigned_area:
            fact.update(domain=assigned_area, assigned_area=assigned_area, domain_is_hint=False)
        # Resolution receipts are server-owned, never accepted as canonical links from a worker.
        for key in ('subject_provisional', 'leaf_provisional', 'leaf_links', 'subject_new', 'leaf_reference_mode'):
            fact.pop(key, None)
        if not leaf_references: fact['leaf_reference_mode'] = 'own'
        # target_ref: the fragment token shown when knowledge was read, [alias@revision]; resolved here, no guessing
        tref = fact.get("target_ref")
        if isinstance(tref, str) and tref.strip() and not re.fullmatch(r"[0-9a-fA-F-]{36}", tref.strip()):
            m = re.fullmatch(r"\[?\s*([^\s@\[\]]+)(?:@(\d+))?\s*\]?", tref.strip())
            row = None
            if m:
                with store_pg.connect(dsn) as conn, conn.cursor() as cur:
                    cur.execute("select id::text,revision from knowledge.fragment where host_id=%s and alias=%s and active", (host, m.group(1)))
                    row = cur.fetchone()
            if not row:
                errors.append({"code": "E_TARGET", "where": f"facts.{index}.target_ref",
                               "detail": "target_ref must be a fragment token as shown in the knowledge window/search, e.g. knowledge-module-16@2"})
                continue
            fact["target_ref"] = row[0]
            if m.group(2):
                fact.setdefault("expected_revision", int(m.group(2)))
        refs = fact.get("evidence_refs")
        if not isinstance(refs, list):
            refs = []
            errors.append({"code": "E_SCHEMA", "where": f"facts.{index}.evidence_refs", "detail": "evidence_refs must be an array"})
        fact["evidence_refs"] = refs
        if not fact.get("evidence_quote"):
            fact["evidence_quote"] = " / ".join(r["quote"] for r in refs if isinstance(r, dict) and isinstance(r.get("quote"), str))
        if not isinstance(fact.get("fact"), str) or not isinstance(fact.get("reason"), str) or not fact["reason"].strip():
            errors.append({"code": "E_SCHEMA", "where": f"facts.{index}", "detail": "fact and nonempty reason are required"})
            continue
        if fact.get("operation") in ("update", "deprecate") and fact.get("target_ref") and data.get("cwd"):
            import worker_tools
            if added is None:
                added = worker_tools._added_lines(data.get("cwd"))
            with store_pg.connect(dsn) as conn, conn.cursor() as cur:
                cur.execute("select text from knowledge.fragment where id::text=%s", (fact["target_ref"],))
                row = cur.fetchone()
            if row:
                seen = {(r.get("type"), r.get("locator"), r.get("quote")) for r in refs if isinstance(r, dict)}
                for r in worker_tools._code_refs({"old": row[0], "new": fact["fact"]}, fact["fact"], added):
                    if (r["type"], r["locator"], r["quote"]) not in seen:
                        refs.append(r); seen.add((r["type"], r["locator"], r["quote"]))
                fact["evidence_refs"] = refs
                fact["evidence_quote"] = " / ".join(r["quote"] for r in refs if isinstance(r, dict) and isinstance(r.get("quote"), str))
        if trusted_identity_refs is None and not runner.subject_in_fact(fact):
            with store_pg.connect(dsn) as conn, conn.cursor() as cur:
                fact=subject_dict.with_known_aliases(cur,host,fact,fact.get('domain') or partition)
        fact, fixes = runner.normalize_fact(fact)  # keywords only; object identity is never inferred
        checked_facts.append({'fact_index': index, 'fact': fact})
        if fixes:
            repairs.append({"fact_index": index, "repairs": fixes})
        metadata = runner.metadata_errors({**fact, 'domain':assigned_area or fact.get('domain'),
                                            'domain_is_hint':not bool(assigned_area)})
        from subject_contract import grounding_errors
        if grounding_check is not None:
            metadata += grounding_check(index, fact)
        elif source_text:
            metadata += grounding_errors(fact, source_text)
        if metadata:
            errors.extend({**e, "where": f"facts.{index}.{e['where']}"} for e in metadata)
            continue
        if "aliases" in fact:  # stage 1 (2026-10-03): other names of the same thing, declared by the parent
            al = fact["aliases"]
            subj_norm = subject_dict.norm(fact.get("subject") or "")
            if (not isinstance(al, list) or len(al) > 10 or any(not isinstance(a, str) or not a.strip() for a in al)
                    or any(subject_dict.norm(a) == subj_norm for a in al)
                    or len({subject_dict.norm(a) for a in al}) != len(al)):
                errors.append({"code": "E_ALIASES", "where": f"facts.{index}.aliases",
                               "detail": "aliases 는 이 사실의 주어와 같은 대상의 다른 이름 목록이다(문자열 10개 이하, 주어와 다르고 중복 없음)"})
                continue
        if trusted_identity_refs is not None:
            fact['subject_provisional'] = {'subject_id':obj['subject_id'],'how':'selected-id'}
        elif isinstance(fact.get("subject"), str) and fact["subject"].strip() and fact.get("operation", "add") != "deprecate":
            name, how = resolve(fact["subject"], fact.get("fact"))
            fact['subject_provisional'] = {**resolve.provisional, 'how': how}
            if resolve.provisional.get('subject_id'):
                fact.update(subject_type=resolve.provisional['subject_type'],
                            subject_description=' '.join(resolve.provisional['description'].splitlines())[:300].strip())
            provisional_subjects.append({'fact_index': index, **fact['subject_provisional'], 'to': name})
            if name != fact['subject'] and fixed_subject:
                errors.append({'code':'E_PLACEMENT_DRIFT','where':f'facts.{index}.subject','detail':'unit-first chosen identity must not be renamed at record'})
                continue
            if name != fact['subject']:
                # Retain paid sentences on short-name reuse; only identity metadata changes.
                fact['aliases']=[a for a in dict.fromkeys([*fact.get('aliases',[]),fact['subject']]) if a != name]
                fact['subject']=name
            if how in subject_dict.NEW:
                fact["subject_new"] = True
        if fact.get("operation", "add") != "deprecate":  # schema-delta '지식 문장 형식': operators between Korean leaves
            import fact_form
            subj_ = fact.get("subject") if isinstance(fact.get("subject"), str) else ""
            if re.search(r"\S(와|과) \S", subj_) or len(subj_) > 30:  # flow3: descriptive subjects split the dictionary
                warnings.append({"code": "W_SUBJECT", "where": f"facts.{index}.subject",
                                 "detail": "주어는 짧은 대상 이름 하나로 쓴다(나열·설명은 문장 안에). 예: 'store_pg.py' 와 'digest_driver.py' 를 따로"})
            head = runner.SUBJECT_HEAD.match((fact_form.parse(fact["fact"]).get("then") or [""])[0] + " ") if isinstance(fact.get("fact"), str) else None
            if subj_ and subj_.rstrip().endswith(("와", "과", "및", ",")):
                # A dangling conjunction is not one object. The owning object may be broader than the sentence head.
                errors.append({"code": "E_SUBJECT", "where": f"facts.{index}.subject",
                               "detail": "주어는 실제 객체 하나(" + (head.group(1).strip() if head else subj_) + ")를 쓴다. 잘린 이름이나 '와/과'로 끝나는 이름은 안 된다"})
            form_errors = fact_form.check(fact.get("fact"))
            for detail in form_errors:
                errors.append({"code": "E_FORM", "where": f"facts.{index}.fact", "detail": detail})
            for detail in fact_form.warnings(fact.get("fact")):  # advice only (user 2026-10-02)
                warnings.append({"code": "W_FORM", "where": f"facts.{index}.fact", "detail": detail})
            if not form_errors:
                fact["form"] = fact_form.parse(fact["fact"])  # 0010: the ledger stores the structure (DB checks its shape)
                hints = []
                for ord_, leaf in enumerate(fact_form.leaves(fact['form'])) if leaf_references else []:
                    head = runner.SUBJECT_HEAD.match(leaf['text'] + ' ')
                    if not head or head.group(1).strip() == fact.get('subject'):
                        continue
                    leaf_name = head.group(1).strip()
                    _, leaf_how = resolve(leaf_name, leaf['text'])
                    hints.append({'ord': ord_, 'text': leaf['text'], **resolve.provisional, 'how': leaf_how})
                fact['leaf_provisional'] = hints
        checked = wc.validate_fact(fact, wc.Contract.PUBLIC_V1)
        for error in checked["errors"]:
            errors.append({**error, "where": f"facts.{index}.{error['where']}"})
        for warning in checked.get("warnings", []):
            warnings.append({**warning, "where": f"facts.{index}.{warning['where']}"})
        operation = fact.get("operation", "add")
        if operation not in ("add", "update", "deprecate"):
            errors.append({"code": "E_OPERATION", "where": f"facts.{index}.operation", "detail": "operation must be add|update|deprecate"})
            continue
        if operation in ("update", "deprecate") and not fact.get("target_ref"):
            errors.append({"code": "E_TARGET", "where": f"facts.{index}.target_ref", "detail": f"{operation} needs target_ref"})
            continue
        if operation in ("update", "deprecate") and not fact.get("expected_revision"):
            # astra review P0-3: without the read revision a concurrent change is overwritten without a merge
            errors.append({"code": "E_TARGET", "where": f"facts.{index}.target_ref",
                           "detail": f"{operation} needs the revision you read: target_ref as [alias@revision] from the knowledge window"})
            continue
        if operation == "deprecate" and fact.get("evidence_source") != "user_confirmed":
            # G2: removing knowledge needs the user's verbatim confirmation (lint_fact then checks it is a non-question).
            errors.append({"code": "E_DEPRECATE_CONFIRM", "where": f"facts.{index}.evidence_source",
                           "detail": "deprecate needs evidence_source user_confirmed with the user's verbatim confirmation"})
            continue
        try:
            excerpt = (worktime_driver._target_excerpt(dsn, host, fact) if operation in ("update", "deprecate")
                       else "이 host 의 정본에 관련 조각이 없다.")
            packet = worktime_driver.build_packet(fact, excerpt)
            for error in runner.lint(packet)["errors"]:
                # Trusted fixed-identity pilot already checked Q1/Q2 and exact raw evidence.
                if fixed_subject and grounding_check is not None and error["code"] == "E_CLAIM_QUOTE": continue
                errors.append({**error, "where": f"facts.{index}.{error['where']}"})
        except (KeyError, TypeError, ValueError) as exc:
            errors.append({"code": "E_PACKET", "where": f"facts.{index}", "detail": type(exc).__name__})
        normalized.append(fact)
        normalized_indices.append(index)
    held = []
    if errors and isolate_invalid:
        by_index = wc.fact_error_groups(errors, len(facts))
        if by_index is not None:
            held = [{'fact_index':i, 'fact':facts[i], 'errors':reasons} for i, reasons in sorted(by_index.items())]
            kept = [(i, f) for i, f in zip(normalized_indices, normalized) if i not in by_index]
            normalized_indices = [i for i, _ in kept]
            normalized = [f for _, f in kept]
            errors = []
            if not normalized:
                return {'ok':False, 'held_facts':held, 'recorded_indices':[], 'errors':[], '_checked_facts': checked_facts}
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings, "repairs": repairs, '_checked_facts': checked_facts,
                "subject_candidates": runner.code_candidates(json.dumps([f.get("evidence_refs",[]) for f in facts if isinstance(f,dict)],ensure_ascii=False))}
    uid = unit_id(data["work_unit_id"]) if data.get("work_unit_id") else str(uuid4())
    body = {"work_unit_id": uid, "host_id": host, "partition_key": partition,
            "judgement_id": str(uuid4()), "version": 1, "baseline_code_ref": baseline_ref(data.get("cwd")), "atomic": bool(data.get("atomic")),
            "facts": normalized}
    if isolate_invalid:
        body["area_validation"] = {"held_facts":held, "recorded_indices":normalized_indices, "assigned_area":assigned_area}
    bound_refs = [trusted_identity_refs[i] for i in normalized_indices] if trusted_identity_refs is not None else None
    entry = store_pg.register(dsn, wc.RecordCommand(body, trusted_identity_refs=bound_refs))
    out = {"held_facts": held, "recorded_indices": normalized_indices, "work_unit_id": uid, "entry_id": entry["entry_id"], "state": "proposed",
           "baseline": snapshot(dsn, uid)["baseline"], "warnings": warnings, "repairs": repairs,
           'domain_proposal': partition, 'provisional_subjects': provisional_subjects,
           'subject_candidates': runner.code_candidates(json.dumps([f.get('evidence_refs',[]) for f in normalized],ensure_ascii=False)),
           'provisional_leaf_links': [{'fact_index': i, **h} for i, f in enumerate(normalized) for h in f.get('leaf_provisional', [])]}
    if held:
        out.update(_checked_facts=checked_facts, _validation_entry=entry)
    if subjects:
        out["subjects"] = subjects
        out["subjects_note"] = "주어를 기존 지식의 이름으로 임시로 맞춰 저장했다(from → to). 도메인·주어 확정과 사전 등록은 소화 액터가 한다. 다른 대상인데 맞춰졌으면 사용자에게 보여 주고 고쳐 다시 기록하라."
    cfg = config.load()
    if data.get("check_contradictions") and all(cfg.get(k) for k in ("jev_api_key", "jev_base_url", "jev_model")):
        found = contradictions(dsn, host, uid, normalized, cfg)
        if found:
            out["contradictions"] = found
            out["ask_user"] = ("이 기록이 이 작업에서 앞서 기록한 사실과 모순된다. 사용자에게 보여 주고 어느 쪽이 맞는지 물어라. "
                              "바뀐 쪽이 맞으면 앞 기록을 고치는 기록을 덧붙이고, 아니면 이 기록을 고쳐 다시 적는다.")
    return out


CONTRA_Q = ("items[{i}] 는 이 작업에서 앞서 기록한 사실이다. state.change 와 동시에 참일 수 없는가? "
            "둘 중 하나가 참이면 다른 하나가 반드시 거짓인 경우에만 true 다(같은 대상의 같은 속성에 서로 다른 값·규칙). 같은 값을 말함, 같은 대상의 다른 측면(예: 하나는 길이 제한, 하나는 라우팅 용도), 하나가 다른 하나를 보완·구체화, 서로 다른 대상이면 false.")


def contradictions(dsn, host, uid, facts, cfg, judge=None):
    """[{fact_index, with, text}]: earlier records of this work unit (the temporary ledger) that this record contradicts.
    The worker asks the user right away. Contradictions with the canon are found after digestion (store_pg.scan_contradictions)."""
    import capture_audit
    judge = judge or capture_audit.make_judge(cfg)
    with store_pg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""select judgement_body from knowledge.ledger_entry where work_unit_id=%s
                    and state::text not in ('closed','expired','rejected_input') order by created_at""", (uid,))
        earlier = [f for (b,) in cur.fetchall() for f in b.get("facts", []) if f.get("fact")]
    import fact_form
    import fact_text
    import subject_dict
    resolved = {}

    def key_of(name):  # astra alias r1: a declared alias and its subject compare as one subject (read only)
        c = subject_dict.core(name)
        if name not in resolved:
            with store_pg.connect(dsn) as conn, conn.cursor() as cur:
                hit = subject_dict.lookup(cur, host, name)
            resolved[name] = ("id:" + hit["subject_id"]) if hit else c
        return resolved[name]
    out = []
    for i, f in enumerate(facts):
        tgt = f.get("target_ref")
        others = [e for e in earlier if e["fact"] != f["fact"] and not (tgt and e.get("target_ref") == tgt)]
        if f.get("form"):
            # schema-delta '잎 단위 모순 검사': the same rule as the canon scan, inside this work unit
            pairs = {}
            for own in _then_leaves(f, key_of):
                for e in others:
                    if e.get("form") and not _comparable(f["form"], e["form"]):
                        continue
                    for other in (_then_leaves(e, key_of) if e.get("form") else [(None, e["fact"], None)]):
                        if other[0] is None or other[0] == own[0] or "*" in (own[0], other[0]):
                            shown = other[1] if other[2] is None else fact_form.display(e["form"], other[1])
                            pairs.setdefault(fact_form.display(f["form"], own[1]), []).append((shown, e["fact"]))
            for own, cands in pairs.items():
                cands = cands[-30:]
                state = {"change": {"operation": f.get("operation", "add"), "fact": own}}
                for (shown, whole), a in zip(cands, judge(state, [s for s, _ in cands], CONTRA_Q)):
                    if capture_audit._noul(a) >= store_pg.CONTRA_KEEP:
                        hit = {"fact_index": i, "with": "이 작업의 앞 기록", "text": fact_text.view(whole)[:300]}
                        if hit not in out:
                            out.append(hit)
            continue
        cands = [("이 작업의 앞 기록", e["fact"]) for e in others][-8:]  # facts without a form (deprecate, legacy)
        if not cands:
            continue
        state = {"change": {"operation": f.get("operation", "add"), "fact": f["fact"]}}
        for (label, text), a in zip(cands, judge(state, [t for _, t in cands], CONTRA_Q)):
            if capture_audit._noul(a) >= store_pg.CONTRA_KEEP:
                out.append({"fact_index": i, "with": label, "text": text[:300]})
    return out


def _then_leaves(f, key_of=None):
    """[(subject key, leaf, form)] of a recorded fact's result sentences; the key is the fact subject when the leaf states
    it, otherwise the leaf's head noun (both through subject_dict.core, nothing is registered at record time)."""
    import subject_dict
    subj = f.get("subject") if isinstance(f.get("subject"), str) else ""
    out = []
    import fact_form
    for leaf in f["form"].get("then", []):
        if fact_form.warnings(leaf):
            # astra warn review: a leaf the parent kept with a Korean connective may state two facts with two subjects;
            # it is compared with every leaf (key '*') so its second fact is not skipped
            out.append(("*", leaf, f["form"]))
            continue
        if subj and subj in leaf:
            key = key_of(subj) if key_of else subject_dict.core(subj)
        else:
            head = runner.SUBJECT_HEAD.match(leaf + " ")
            key = (key_of(head.group(1).strip()) if key_of else subject_dict.core(head.group(1).strip())) if head else None
        out.append((key, leaf, f["form"]))
    return out


def _comparable(a, b):
    import fact_form
    return fact_form.comparable(a, b)


def complete(dsn, data):
    uid = unit_id(data.get("work_unit_id"))
    quote = required(data.get("user_quote"), "user_quote")
    if runner.QUESTION_TAIL.search(quote.strip()):
        return {"ok": False, "errors": [{"code": "E_CONFIRM", "where": "user_quote",
                  "detail": "Ask for explicit user confirmation first; a question is not confirmation"}]}
    locator = data.get("locator")
    if locator is not None and not isinstance(locator, str):
        raise ValueError("locator must be a string")
    state = snapshot(dsn, uid)
    if state["status"] == "active":
        with store_pg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("select completion_sources from knowledge.work_unit where work_unit_id=%s", (uid,))
            sources = cur.fetchone()[0]
        if not sources:
            store_pg.register_completion_sources(dsn, uid, ["user_confirm"])
    result = store_pg.signal_completion(dsn, uid, "user_confirm", evidence={"quote": quote, "locator": locator})
    state = snapshot(dsn, uid)
    result["entry_states"] = state["entry_states"]
    if state["entry_states"]["provisional"] == 0 and state["entry_states"]["proposed"]:
        result["notice"] = "작업 중 검수 대기 중 — 소화자가 곧 처리"
    with store_pg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select host_id from knowledge.work_unit where work_unit_id=%s", (uid,))
        host = cur.fetchone()[0]
    waiting = [i for i in store_pg.review_list(dsn, host) if i["work_unit_id"] == uid]
    if waiting:
        result["review_pending"] = len(waiting)
        result["review_notice"] = "애매한 사실이 있어 정본 반영 전 사용자 확인 필요 — knowledge_review list 후 사용자에게 물어서 처리"
    return result


def tools(dsn, command, data, *, answer_review_policy=None):
    """Worker tools (worker_tools.py): search | fix_plan | fix_submit. Jev config is required for all three."""
    import worker_tools
    cfg = config.require("jev_api_key", "jev_base_url", "jev_model")
    ask = worker_tools.make_ask(cfg)
    host = data.get("host_id") or config.load()["default_host"]
    required(host, "host_id (set default_host in knowledge.json)")
    if command == "search":
        allowed = {"question", "queries", "host_id", "layer", "change", "answer_evidence"}
        if set(data) - allowed:
            raise ValueError("invalid knowledge_search argument")
        from answer_review_adapter import bound
        bound(answer_review_policy, host)
        answer_request = None
        if data.get('answer_evidence') is True:
            import capture_audit
            answer_request = lambda body: capture_audit.request_answers(cfg, body, max_attempts=1)
        return worker_tools.search(dsn, host, data.get("question"), data.get("queries"), ask, layer=data.get("layer"),
                                   change=data.get("change"), answer_request=answer_request, answer_model=cfg['jev_model'],
                                   answer_authority=config.source_authority(cfg, host) if answer_request else None,
                                   answer_review_policy=answer_review_policy)
    if command == "fix_plan":
        return worker_tools.fix_plan(dsn, host, data.get("change"), data.get("queries"), ask)
    return worker_tools.fix_submit(dsn, data.get("plan_id"), data.get("answers"), ask, lambda d: record(dsn, d),
                                   partition_key=required(data.get("partition_key"), "partition_key"),
                                   work_unit_id=data.get("work_unit_id"), cwd=data.get("cwd"),
                                   precheck=lambda fact: lint_update_fact(dsn, host, fact))


def audit(dsn, data):
    """Capture audit (capture_audit.py): transcript vs facts already recorded in this work unit -> missing list."""
    import capture_audit
    cfg = config.require("jev_api_key", "jev_base_url", "jev_model")
    recorded = list(data.get("recorded_facts") or [])
    if data.get("work_unit_id"):
        uid = unit_id(data["work_unit_id"])
        with store_pg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("select judgement_body from knowledge.ledger_entry where work_unit_id=%s", (uid,))
            for (body,) in cur.fetchall():
                recorded += [f.get("fact") for f in (body or {}).get("facts", []) if isinstance(f, dict)]
    return capture_audit.audit(data.get("transcript"), recorded, capture_audit.make_judge(cfg))


def domain_cmd(dsn, data):
    """knowledge_cli domain: list | show {domain} | merge {src,dst} | retire {domain} | describe {domain,description}."""
    import domain_router
    host = data.get("host_id") or config.load()["default_host"]
    required(host, "host_id (set default_host in knowledge.json)")
    action = data.get("action", "list")
    with store_pg.connect(dsn) as conn, conn.cursor() as cur:
        if action == "describe":
            domain_router.describe(cur, host, data.get("domain"), data.get("description"))
        elif action == "merge":
            domain_router.merge(cur, host, required(data.get("src"), "src"), required(data.get("dst"), "dst"))
        elif action == "retire":
            domain_router.retire(cur, host, required(data.get("domain"), "domain"))
        elif action == "show":
            return {"domain": required(data.get("domain"), "domain"),
                    "fragments": domain_router.fragments(cur, host, data["domain"])}
        elif action != "list":
            raise ValueError("action must be list|show|merge|retire|describe")
        return {"domains": domain_router.list_domains(cur, host)}


REASON_TEXT = {"durability": "오래 쓸 지식인지 애매", "is_supported": "근거가 충분한지 애매",
               "is_new": "이미 있는 지식과 겹치는지 애매", "should_record": "기록할 가치가 있는지 애매",
               "digest_mismatch": "작업 중 검사와 소화 재검사 판정이 다름", "impact": "앞으로의 판단에 영향이 있는지 애매",
               "scope": "근거가 담은 범위와 주장 범위가 같은지 애매", "scope_expanded": "주장이 근거보다 넓음(근거에 없는 내용 포함)",
               "correction_evidence": "고침을 뒷받침하는 근거가 충분한지 애매", "differs_from_target": "기존 조각과 실제로 다른지 애매",
               "invalidation_evidence": "폐기 근거가 충분한지 애매", "replacement_exists": "대체할 지식이 있는지 애매",
               "missing v0.3 response": "판정 응답 빠짐",
               "stale_base": "읽은 뒤 다른 작업이 먼저 바꿈 — 어느 쪽이 맞나",
               "atomic_wait": "같은 변경의 다른 수정이 확인을 기다림"}


def _reason_text(reason):
    r = str(reason)
    if r.startswith("scope: expanded"):
        return REASON_TEXT["scope_expanded"]
    if r.startswith("canon_contradiction:"):
        return "정본에서 이 사실과 모순:" + r.split(":", 1)[1]
    if r.startswith("alias_conflict:"):
        return "두 주어가 같은 대상인지 사용자 확인 필요:" + r.split(":", 1)[1]
    return REASON_TEXT.get(r.split(":")[0], REASON_TEXT.get(r, r))


def review_cmd(dsn, data, *, runtime_db=None):
    """knowledge_cli review: list | resolve {entry_id, fact_index, decision approve|reject, user_quote, locator?}
    | resolve alias_conflict {decision same|different|defer, question_ids [one], user_quote} | undo_merge {merge_id, user_quote}."""
    from runtime_db import bind_runtime_db
    runtime_db = bind_runtime_db(runtime_db or dsn)
    dsn = runtime_db.require_same_target(dsn)
    host = data.get("host_id") or config.load()["default_host"]
    required(host, "host_id (set default_host in knowledge.json)")
    action = data.get("action", "list")
    if action == "list":
        source_review_status = None
        verified_source_receipts = {}
        root = data.get("source_root")
        if data.get("require_source_review") and isinstance(root, str) and root:
            import source_evidence
            import source_review
            import capture_audit
            cfg = config.require("jev_api_key", "jev_base_url", "jev_model")
            bindings = (source_evidence.load_import_bindings(root, data["source_import_manifest"])
                        if data.get("source_import_manifest") else {})
            provider = source_review.SourceProvider(root, bindings=bindings)
            judge = capture_audit.make_judge(cfg)
            reviewer = lambda source_host, left, right: (source_review.review_pair(left, right, provider, judge)
                if source_host == data.get("source_host", host) else {"status": "evidence_needed", "reason": "source_root_host_mismatch"})
            source_review_status = store_pg.source_review_pending(dsn, reviewer, limit=25, host=host)
            verified_source_receipts = source_review_status["verified_conflicts"]
        items = store_pg.review_list(dsn, host, require_source_review=bool(data.get("require_source_review")),
                                    verified_source_receipt_ids=verified_source_receipts)
        for item in items:
            item["why_waiting"] = [_reason_text(r) for r in item["review_reasons"] or []]
        return {"items": items, **({"source_review": source_review_status} if source_review_status is not None else {})}
    if action == "metadata_repair":
        import source_evidence, source_metadata_repair
        if data.get("source_host", host) != host:
            raise ValueError("metadata-repair source host must match the fragment host")
        fragment_id = required(data.get("fragment_id"), "fragment_id")
        expected_revision = data.get("expected_revision")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        root = data.get("source_root")
        if not isinstance(root, str) or not root:
            raise ValueError("metadata_repair requires an explicit source_root")
        manifest = data.get("source_import_manifest")
        bindings = source_evidence.load_import_bindings(root, manifest) if manifest else {}
        with store_pg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("""select id::text,host_id,alias,domain,seq,text,keywords,kind::text,group_id,revision,active,
                        confidence::text,source,valid_from,superseded_at,subject_id::text from knowledge.fragment
                        where host_id=%s and id=%s""", (host, fragment_id))
            fragment = store_pg._row(cur)
        if not fragment or not fragment["active"] or fragment["revision"] != expected_revision:
            return {"status": "stale"}
        proposal = source_metadata_repair.prepare(fragment, data.get("new_evidence_refs"), root,
            bindings=bindings, expected_host=host)
        return store_pg.apply_source_metadata_repair(dsn, host, proposal, source_root=root, bindings=bindings)
    if action == "source_hint":
        import source_evidence, source_review
        question_id = data.get("question_id")
        if not isinstance(question_id, int) or isinstance(question_id, bool):
            raise ValueError("question_id must be an integer")
        ids = [required(data.get("left_fragment_id"), "left_fragment_id"), required(data.get("right_fragment_id"), "right_fragment_id")]
        expected = [data.get("left_revision"), data.get("right_revision")]
        if any(not isinstance(v, int) or isinstance(v, bool) or v < 1 for v in expected):
            raise ValueError("left_revision and right_revision must be positive integers")
        root = data.get("source_root") or os.getcwd()
        bindings = source_evidence.load_import_bindings(root, data["source_import_manifest"]) if data.get("source_import_manifest") else {}
        with store_pg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("select id::text,revision,text,source,active from knowledge.fragment where host_id=%s and id=any(%s::uuid[])",
                        (host, ids))
            fragments = {r["id"]: r for r in store_pg._rows(cur)}
        left, right = fragments.get(ids[0]), fragments.get(ids[1])
        if not left or not right or [left["revision"], right["revision"]] != expected:
            return {"status": "stale"}
        proposal = source_review.review_pair_from_agent(left,right,source_review.SourceProvider(root,bindings=bindings),
            verdict=data.get("source_verdict"), explanation=required(data.get("source_reason"),"source_reason"),
            source_quotes=data.get("source_quotes"), opposed_assertions=data.get("opposed_assertions"),
            same_scope=data.get("same_scope"))
        if proposal.get("status") != "reviewed":
            return proposal
        return store_pg.resolve_source_hint(dsn,host,question_id,proposal,data.get("outcome"))
    if action == "source_correct":
        import source_correction, source_evidence, source_review, capture_audit
        fragment_id = required(data.get("fragment_id"), "fragment_id")
        expected_revision = data.get("expected_revision")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        root = data.get("source_root") or os.getcwd()
        manifest = data.get("source_import_manifest")
        bindings = source_evidence.load_import_bindings(root, manifest) if manifest else {}
        with store_pg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("select id::text,revision,text,source,active from knowledge.fragment where host_id=%s and id=%s",
                        (host, fragment_id))
            fragment = store_pg._row(cur)
        if not fragment or not fragment["active"] or fragment["revision"] != expected_revision:
            return {"status": "stale"}
        provider = source_review.SourceProvider(root, bindings=bindings)
        corrected_text = required(data.get("corrected_text"), "corrected_text")
        agent_proposal = source_correction.propose_agent_correction(fragment, corrected_text, provider,
            explanation=required(data.get("source_reason"), "source_reason"), source_quotes=data.get("source_quotes"),
            temporal_scope=data.get("temporal_scope"))
        if agent_proposal.get("status") != "supported":
            return agent_proposal
        cfg = config.require("jev_api_key", "jev_base_url", "jev_model")
        scope = data.get("temporal_scope")
        review_context = ({**scope, "source_reason": required(data.get("source_reason"), "source_reason"),
                           "source_quotes": data.get("source_quotes"),
                           "proposal_provenance": agent_proposal["evidence"]}
                          if isinstance(scope, dict) else None)
        verified = source_correction.review_correction(fragment, corrected_text, provider,
            capture_audit.make_judge(cfg), review_context=review_context)
        if verified.get("status") != "supported":
            return verified
        return store_pg.apply_source_correction(dsn, host, verified, source_root=root, bindings=bindings)
    if action not in ("resolve", "undo_merge"):
        raise ValueError("action must be list|resolve|undo_merge|source_hint|source_correct")
    quote = required(data.get("user_quote"), "user_quote")
    if runner.QUESTION_TAIL.search(quote.strip()):
        return {"ok": False, "errors": [{"code": "E_CONFIRM", "where": "user_quote",
                  "detail": "Ask the user first; a question is not a decision"}]}
    import subject_merge  # stage 2 (2026-10-03): merging two subjects only on the user's answer, undoable
    if action == "undo_merge":
        return subject_merge.undo_merge(dsn, host, unit_id(data.get("merge_id")), quote, data.get("locator"))
    if data.get("decision") in ("same", "different", "defer"):
        qids = data.get("question_ids")
        if not (isinstance(qids, list) and len(qids) == 1 and isinstance(qids[0], int) and not isinstance(qids[0], bool)):
            raise ValueError("alias_conflict: question_ids must be the one id listed")
        return subject_merge.answer_conflict(dsn, host, qids[0], data["decision"], quote, data.get("locator"))
    index = data.get("fact_index")
    if not isinstance(index, int) or isinstance(index, bool):
        raise ValueError("fact_index must be an integer")
    locator = data.get("locator")
    if locator is not None and not isinstance(locator, str):
        raise ValueError("locator must be a string")
    qids = data.get("question_ids")
    if qids is not None and (not isinstance(qids, list) or not all(isinstance(q, int) and not isinstance(q, bool) for q in qids)):
        raise ValueError("question_ids must be a list of integers as listed")
    return store_pg.review_resolve(dsn, host, unit_id(data.get("entry_id")), index, data.get("decision"), quote, locator, qids)


def subject_cmd(runtime_db, host, data):
    from subject_inspection import inspect_subjects
    return inspect_subjects(store_pg.connect, runtime_db, host, data)


COMMANDS = ("record", "complete", "status", "search", "more", "brief", "fix_plan", "fix_submit", "audit", "domain", "review", "window", "subject")


def main():
    try:
        args = list(sys.argv[1:])
        dsn_override = None
        review_policy = None
        if "--answer-review-stdio" in args:
            index = args.index("--answer-review-stdio")
            if args.count("--answer-review-stdio") != 1 or len(args[index + 1:index + 4]) != 3:
                raise ValueError("invalid host answer review selection")
            from answer_review_ipc import stdio_policy
            review_policy = stdio_policy(*args[index + 1:index + 4])
            del args[index:index + 4]
        if "--dsn" in args:
            index = args.index("--dsn")
            if index + 1 >= len(args) or args.count("--dsn") != 1:
                raise ValueError("--dsn requires one connection string")
            dsn_override = args[index + 1]
            del args[index:index + 2]
        if len(args) != 1 or args[0] not in COMMANDS:
            raise ValueError("expected " + "|".join(COMMANDS) + " [--dsn CONNECTION]")
        if review_policy is not None and args[0] != "search":
            raise ValueError("answer review selection is search-only")
        from runtime_db import bind_runtime_db
        # Subject reads freeze current host and DB from ONE server configuration snapshot.
        subject_cfg = config.require(*(('default_host',) if dsn_override else ('db_url', 'default_host'))) if args[0] == 'subject' else None
        config_dsn = (subject_cfg['db_url'] if subject_cfg is not None else config.require("db_url")["db_url"]) if dsn_override is None else None
        runtime_db = bind_runtime_db(dsn_override or config_dsn,
                                     provenance="explicit-cli" if dsn_override else "configured-entry")
        data = json.load(sys.stdin)
        if not isinstance(data, dict):
            raise ValueError("input must be an object")
        dsn = runtime_db.dsn
        command = args[0]
        if command == 'subject':
            result = subject_cmd(runtime_db, subject_cfg['default_host'], data)
        elif command in ("search", "fix_plan", "fix_submit"):
            result = tools(dsn, command, data, answer_review_policy=review_policy)
        elif command == "brief":
            import brief, worker_tools
            cfg = config.require("jev_api_key", "jev_base_url", "jev_model")
            host = data.get("host_id") or config.load()["default_host"]
            result = brief.brief(dsn, required(host, "host_id"), data.get("question"), data.get("queries"),
                                 worker_tools.make_ask(cfg), change=data.get("change"))
        elif command == "more":
            import worker_tools
            result = worker_tools.more(dsn, required(data.get("search_id"), "search_id"), domain=data.get("domain"), group=data.get("group"))
        elif command == "audit":
            result = audit(dsn, data)
        elif command == "domain":
            result = domain_cmd(dsn, data)
        elif command == "review":
            if data.get("action", "list") == "list":
                data = {**data, "require_source_review": True, "source_root": os.getcwd()}
            result = review_cmd(dsn, data, runtime_db=runtime_db)
        elif command == "window":
            import window, worker_tools
            cfg = config.require("jev_api_key", "jev_base_url", "jev_model")
            host = data.get("host_id") or config.load()["default_host"]
            result = window.build(dsn, required(host, "host_id"), data.get("question"), data.get("queries"),
                                  worker_tools.make_ask(cfg))
        else:
            result = record(dsn, data) if command == "record" else (complete(dsn, data) if command == "complete" else snapshot(dsn, unit_id(data.get("work_unit_id"))))
    except wc.WriteContractError as exc:
        result = exc.as_result()
    except ValueError as exc:
        result = {"ok": False, "error": "ValueError", "detail": str(exc)[:200]}
    except Exception as exc:
        # Driver exceptions may embed credentials or connection routing details; expose class only.
        result = {"ok": False, "error": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
