"""Stage 2 of resolving aliases in the subject dictionary: merging two existing subjects (2026-10-03, user 'a').

A declared alias that already names another subject leaves an alias_conflict question. Only the user's answer merges:
same -> the two subjects become one (fragment/leaf subject_id moved in place, no revision bump: option A, user); different
-> the question closes, nothing changes; defer -> nothing. Every moved row is logged (subject_merge_item, with a guard of
the row as merged) so an undo reverts exactly those rows. Lock order (astra merge r1 P1-4): the host dictionary lock
first -- digestion and backfill take it exclusive at transaction start, merge/undo exclusive, scans and listings shared --
then rows. Merge/undo bump the host's subject generation (a judgement made before it is not stored after it) and queue
the affected fragments for a rescan. Design: .local/merge-design.md + astra design and code reviews.
"""
import json

import store_pg as pg


def lock_host(cur, host, exclusive):
    fn = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    cur.execute(f"select {fn}(hashtextextended(%s, 0))", (f"lh-dict/{host}",))


def ensure_generation(cur, host):
    cur.execute("insert into knowledge.subject_generation(host_id) values (%s) on conflict do nothing", (host,))


def generation(cur, host, share=False, domain=None):
    if domain is not None:
        cur.execute('select generation from knowledge.domain_type where host_id=%s and domain=%s', (host,domain))
        row = cur.fetchone()
        return row[0] if row else 0
    cur.execute("select generation from knowledge.subject_generation where host_id=%s" + (" for share" if share else ""),
                (host,))
    row = cur.fetchone()
    return row[0] if row else 0


def domain_of(cur, subject):
    cur.execute('select domain from knowledge.subject where subject_id=%s', (subject,))
    return cur.fetchone()[0]


def _bump(cur, host, domain=None):
    cur.execute('''update knowledge.domain_type set generation=generation+1
        where host_id=%s and (%s::text is null or domain=%s)''', (host,domain,domain))
    cur.execute("""insert into knowledge.subject_generation(host_id, generation) values (%s, 1)
                   on conflict (host_id) do update set generation = knowledge.subject_generation.generation + 1
                   returning generation""", (host,))
    return cur.fetchone()[0]


def root(cur, subject_id):
    """(root id, root name) of a subject; a merged subject points at the subject it was merged into (one hop: a merge
    re-points every subject already merged into the one it absorbs)."""
    if not subject_id:
        return None, None
    cur.execute("""select r.subject_id::text, r.name from knowledge.subject s
                   join knowledge.subject r on r.subject_id = coalesce(s.merged_into, s.subject_id)
                   where s.subject_id=%s""", (subject_id,))
    row = cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


def members(cur, subject_id):
    """The subjects that read as this root (itself and every subject merged into it), sorted -- a question binds to
    these sets (astra merge r1 P0-2: a survivor that grew since the question was shown needs a new question)."""
    if not subject_id:
        return []
    cur.execute("""select subject_id::text from knowledge.subject where subject_id=%s or merged_into=%s order by 1""",
                (subject_id, subject_id))
    return [r[0] for r in cur.fetchall()]


def queue_rescan(cur, host, subject_ids, gen, reason):
    """Every active fragment of these subjects, and every active fragment with a current leaf about them."""
    cur.execute("""insert into knowledge.fragment_rescan(host_id, fragment_id, generation, reason)
                   select distinct f.host_id, f.id, %s, %s::jsonb from knowledge.fragment f
                   where f.host_id=%s and f.active and (f.subject_id = any(%s::uuid[]) or exists (
                     select 1 from knowledge.fragment_leaf l where l.fragment_id=f.id and l.revision=f.revision
                     and l.subject_id = any(%s::uuid[])))
                   on conflict do nothing""", (gen, json.dumps(reason), host, list(subject_ids), list(subject_ids)))
    return cur.rowcount


def _norm(text):
    return " ".join(str(text or "").split())


def answer_conflict(dsn, host, question_id, decision, quote, locator=None):
    """The user's answer to one alias_conflict question (exactly the question listed)."""
    if decision not in ("same", "different", "defer"):
        raise ValueError("alias_conflict decision must be same|different|defer")
    if not _norm(quote):
        raise ValueError("user_quote is required")
    with pg.connect(dsn) as conn, conn.cursor() as cur:
        lock_host(cur, host, exclusive=decision == "same")
        cur.execute("""select q.reason, q.status::text, e.host_id, w.completion_sources
                       from knowledge.confirmation_queue q join knowledge.ledger_entry e on e.entry_id=q.entry_id
                       join knowledge.work_unit w on w.work_unit_id=e.work_unit_id
                       where q.confirmation_id=%s and q.rule_id='alias_conflict' for update of q""", (question_id,))
        row = cur.fetchone()
        if not row or row[2] != host:
            raise ValueError("alias_conflict question not found for host")
        if row[1] != "pending":
            raise ValueError("this question is already closed; list again")
        r = json.loads(row[0])
        used = {_norm((s.get("evidence") or {}).get("quote")) for s in (row[3] or []) if isinstance(s, dict)}
        if decision != "defer" and _norm(quote) in used:
            raise ValueError("the completion confirmation cannot answer this question; ask the user and quote their answer")
        if decision == "defer":
            return {"question_id": question_id, "decision": "defer", "next": "Kept; it will be listed again."}
        a, b = r.get("subject_id"), r.get("other_id")
        cur.execute("""select subject_id::text, name, merged_into::text, created_at from knowledge.subject
                       where host_id=%s and subject_id = any(%s::uuid[]) for update""", (host, [a, b]))
        subs = {s[0]: s for s in cur.fetchall()}
        if (a == b or len(subs) != 2 or subs[a][2] or subs[b][2]
                or members(cur, a) != r.get("members_a", [a]) or members(cur, b) != r.get("members_b", [b])):
            raise ValueError("the two subjects changed since this question was listed; list again and ask the user")
        answer = {"decision": decision, "quote": quote, "locator": locator}
        if decision == "different":
            cur.execute("""update knowledge.confirmation_queue set status='answered', answer=%s, answered_at=now()
                           where confirmation_id=%s""", (json.dumps(answer, ensure_ascii=False), question_id))
            return {"question_id": question_id, "decision": "different",
                    "next": "No merge. Correct the aliases of that fact with knowledge_record update if they were wrong."}
        cur.execute("""select subject_id::text, count(*) from knowledge.fragment where host_id=%s and active
                       and subject_id = any(%s::uuid[]) group by 1""", (host, [a, b]))
        counts = dict(cur.fetchall())
        # survivor: more active fragments, then the older subject
        into = sorted([a, b], key=lambda s: (-counts.get(s, 0), subs[s][3], s))[0]
        frm = b if into == a else a
        parent_items = parent_merge_items(cur, host, frm, into)
        shown = {"subjects": [{"id": a, "name": subs[a][1], "members": r.get("members_a", [a])},
                              {"id": b, "name": subs[b][1], "members": r.get("members_b", [b])}], "alias": r.get("alias")}
        cur.execute("""insert into knowledge.subject_merge(host_id, from_subject, into_subject, confirmation_id, shown,
                       approved_quote, locator) values (%s,%s,%s,%s,%s,%s,%s) returning merge_id::text""",
                    (host, frm, into, question_id, json.dumps(shown, ensure_ascii=False), quote, locator))
        mid = cur.fetchone()[0]
        cur.execute("select set_config('knowledge.merge_id', %s, true)", (mid,))
        items = list(parent_items)
        cur.execute("""select alias, kind, source_fragment_id::text, source_revision, merged_by::text, domain from knowledge.subject_alias
                       where host_id=%s and subject_id=%s for update""", (host, frm))
        items += [("subject_alias", {"alias": x[0], "merged_by_before": x[4], "domain": x[5]}, frm,
                   {"kind": x[1], "source_fragment_id": x[2], "source_revision": x[3]}) for x in cur.fetchall()]
        cur.execute("select subject_id::text from knowledge.subject where merged_into=%s for update", (frm,))
        items += [("subject", {"subject_id": x[0]}, frm, {}) for x in cur.fetchall()]
        items.append(("subject", {"subject_id": frm}, None, {}))
        cur.execute("""select id::text, revision, form is not null from knowledge.fragment where host_id=%s and subject_id=%s
                       for update""", (host, frm))
        items += [("fragment", {"id": x[0]}, frm, {"revision": x[1], "structured": x[2]}) for x in cur.fetchall()]
        cur.execute("""select fragment_id::text, revision, ord from knowledge.fragment_leaf where subject_id=%s for update""",
                    (frm,))
        items += [("fragment_leaf", {"fragment_id": x[0], "revision": x[1], "ord": x[2]}, frm, {}) for x in cur.fetchall()]
        for seq, (tbl, key, old, guard) in enumerate(items):
            cur.execute("""insert into knowledge.subject_merge_item(merge_id, seq, tbl, row_key, old_subject, new_subject, guard)
                           values (%s,%s,%s,%s,%s,%s,%s)""", (mid, seq, tbl, json.dumps(key), old, into, json.dumps(guard)))
        cur.execute("update knowledge.subject_alias set subject_id=%s, merged_by=%s where host_id=%s and subject_id=%s",
                    (into, mid, host, frm))
        cur.execute('update knowledge.subject set parent_subject_id=%s where parent_subject_id=%s',(into,frm))
        cur.execute("update knowledge.subject set merged_into=%s where merged_into=%s", (into, frm))
        cur.execute("update knowledge.subject set merged_into=%s where subject_id=%s", (into, frm))
        cur.execute("update knowledge.fragment set subject_id=%s where host_id=%s and subject_id=%s", (into, host, frm))
        cur.execute("update knowledge.fragment_leaf set subject_id=%s where subject_id=%s", (into, frm))
        gen = _bump(cur, host, domain_of(cur, frm))
        queued = queue_rescan(cur, host, [into], gen, {"merge": mid})
        cur.execute("update knowledge.subject_merge set state='done', generation_after=%s where merge_id=%s", (gen, mid))
        cur.execute("""update knowledge.confirmation_queue set status='answered', answer=%s, answered_at=now()
                       where confirmation_id=%s""", (json.dumps({**answer, "merge_id": mid}, ensure_ascii=False), question_id))
        return {"question_id": question_id, "decision": "same", "merge_id": mid,
                "merged": {"from": subs[frm][1], "into": subs[into][1]}, "moved_rows": len(items), "rescan": queued,
                "next": f"Merged '{subs[frm][1]}' into '{subs[into][1]}'. If the user later says this was wrong, "
                        f"knowledge_review action=undo_merge merge_id={mid} with their words."}


def ambiguity(verdict, probability):
    """Luna must compare descriptions and cite concrete evidence; Jev is typed confidence."""
    return (isinstance(verdict, dict) and verdict.get('relation') == 'same'
            and isinstance(verdict.get('description_comparison'), str)
            and bool(verdict['description_comparison'].strip())
            and isinstance(verdict.get('evidence'), list) and bool(verdict['evidence'])
            and all(isinstance(e, dict) and e.get('locator') and e.get('quote') for e in verdict['evidence'])
            and isinstance(probability, (int, float)) and not isinstance(probability, bool)
            and .8 <= probability <= 1)


def judge_merge(cur, host, kind, pair, snapshot, luna, same):
    """Only new evidence re-judges a hold. Third distinct hold enters the review list."""
    import hashlib
    fingerprint = hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()
    pair = sorted(map(str, pair))
    cur.execute('''select fragment_fingerprint,verdict,holds,review_list from knowledge.merge_judgement
        where host_id=%s and kind=%s and pair=%s order by id desc limit 1''', (host, kind, pair))
    previous = cur.fetchone()
    if previous and previous[0] == fingerprint:
        return {'status': 'hold', 'holds': previous[2], 'review_list': previous[3], 'cached': True}
    from model_output import ModelOutputInvalid
    from digest_driver import jev_comparison
    probability = same(jev_comparison(snapshot))
    luna_called = probability >= 0.8
    if luna_called:
        try:
            verdict = luna(snapshot)
        except ModelOutputInvalid as exc:
            verdict = {'relation': 'unknown', 'description_comparison': str(exc), 'evidence': []}
    else:
        verdict = {'relation':'different', 'description_comparison':'Jev identity prefilter; Luna skipped', 'evidence':[]}
    accepted = ambiguity(verdict, probability)
    holds = (previous[2] if previous else 0) + (not accepted)
    cur.execute('''insert into knowledge.merge_judgement(host_id,kind,pair,fragment_fingerprint,verdict,holds,review_list)
        values(%s,%s,%s,%s,%s,%s,%s)''',
        (host, kind, pair, fingerprint, json.dumps({'luna': verdict, 'jev': probability, 'luna_called':luna_called}), holds, holds >= 3 and not accepted))
    return {'status': 'same' if accepted else 'hold', 'luna_called':luna_called, 'jev_calls':1, 'holds': holds, 'review_list': holds >= 3 and not accepted,
            'reason': {'relation': verdict.get('relation') if isinstance(verdict, dict) else 'unknown',
                       'description_comparison': verdict.get('description_comparison') if isinstance(verdict, dict) else None,
                       'jev': probability}, 'evidence': verdict.get('evidence', []) if isinstance(verdict, dict) else []}


def merge_review_list(cur, host):
    cur.execute('''select id,kind,pair,holds from (select distinct on(kind,pair)
        id,kind,pair,holds,review_list from knowledge.merge_judgement
        where host_id=%s order by kind,pair,id desc) latest where review_list''',(host,))
    return [{'kind':'merge_review','merge_kind':kind,'pair':pair,'holds':holds,'judgement_id':i,
             'entry_id':None,'fact_index':None,'work_unit_id':None,'entry_state':'held',
             'fact':' / '.join(pair),'question_ids':[],
             'review_reasons':['merge_hold: 세 차례 보류 — 새 근거 또는 사람 검토 필요']}
            for i,kind,pair,holds in cur.fetchall()]


def parent_merge_items(cur, host, frm, into):
    """Only siblings merge; root merges move children with reversible receipts."""
    from subject_contract import PARENT_TYPES
    cur.execute('select subject_id::text,parent_subject_id::text,subject_type,domain from knowledge.subject where host_id=%s and subject_id=any(%s::uuid[]) for update', (host,[frm,into]))
    rows={r[0]:r for r in cur.fetchall()}
    if len(rows)!=2 or rows[frm][1]!=rows[into][1] or rows[frm][3]!=rows[into][3]:
        raise ValueError('E_SUBJECT_PARENT: merge requires same domain and same parent')
    cur.execute('select subject_id::text from knowledge.subject where parent_subject_id=%s for update',(frm,))
    children=[r[0] for r in cur.fetchall()]
    if children and (rows[into][1] or rows[into][2] not in PARENT_TYPES or into in children):
        raise ValueError('E_SUBJECT_PARENT: merge would invalidate parent type/depth/cycle')
    return [('subject',{'subject_id':sid,'field':'parent_subject_id'},frm,{}) for sid in children]


def automatic_merge(cur, host, frm, into, luna, same):
    """Domain actor's protected subject merge. Existing immutable receipts support undo."""
    import domain_router
    cur.execute('select domain from knowledge.subject where host_id=%s and subject_id=%s', (host, frm))
    row = cur.fetchone()
    if not row:
        raise ValueError('subject not found')
    domain_router.lock_partition(cur, host, row[0])
    cur.execute('''select to_jsonb(s) from knowledge.subject s where host_id=%s
        and subject_id=any(%s::uuid[]) order by subject_id for update''', (host, [frm, into]))
    subjects = [r[0] for r in cur.fetchall()]
    if len(subjects) != 2 or any(s['domain'] != row[0] or s['merged_into'] for s in subjects):
        raise ValueError('automatic merge requires two current subjects in the same domain')
    try:
        parent_items = parent_merge_items(cur, host, frm, into)
    except ValueError as exc:
        return {'status':'hold','reason':str(exc),'code':'E_SUBJECT_PARENT'}
    cur.execute('''select id::text,revision,text,source from knowledge.fragment where host_id=%s
        and subject_id=any(%s::uuid[]) order by id''', (host, [frm, into]))
    fragments = cur.fetchall()
    parents = [s['parent_subject_id'] for s in subjects if s.get('parent_subject_id')]
    names = {}
    if parents:
        cur.execute('select subject_id::text,name from knowledge.subject where host_id=%s and subject_id=any(%s::uuid[])', (host, parents))
        names = dict(cur.fetchall())
    snapshot = {'subjects': [{**{k:s[k] for k in ('subject_id','name','domain','description','description_evidence','merged_into','subject_type','parent_subject_id')},
                              'parent_name': names.get(str(s.get('parent_subject_id')))}
                             for s in subjects], 'fragments': fragments}
    result = judge_merge(cur, host, 'subject', [frm, into], snapshot, luna, same)
    if result['status'] != 'same':
        return result
    cur.execute('''insert into knowledge.subject_merge(host_id,from_subject,into_subject,shown,approved_quote,actor,reason,evidence)
        values(%s,%s,%s,%s,'automatic','luna-auto',%s,%s) returning merge_id::text''',
        (host, frm, into, json.dumps(snapshot, default=str), json.dumps(result['reason']), json.dumps(result['evidence'])))
    mid = cur.fetchone()[0]
    cur.execute("select set_config('knowledge.merge_id',%s,true)", (mid,))
    items = list(parent_items)
    cur.execute('''select alias,kind,source_fragment_id::text,source_revision,merged_by::text,domain
        from knowledge.subject_alias where host_id=%s and subject_id=%s for update''', (host, frm))
    items += [('subject_alias', {'alias': a, 'domain': d, 'merged_by_before': m}, frm,
               {'kind': k, 'source_fragment_id': f, 'source_revision': r}) for a,k,f,r,m,d in cur.fetchall()]
    cur.execute('select subject_id::text from knowledge.subject where merged_into=%s for update', (frm,))
    items += [('subject', {'subject_id': r[0]}, frm, {}) for r in cur.fetchall()]
    items.append(('subject', {'subject_id': frm}, None, {}))
    cur.execute('select id::text,revision,form is not null from knowledge.fragment where host_id=%s and subject_id=%s for update', (host, frm))
    items += [('fragment', {'id': f}, frm, {'revision': r, 'structured': s}) for f,r,s in cur.fetchall()]
    cur.execute('select fragment_id::text,revision,ord from knowledge.fragment_leaf where subject_id=%s for update', (frm,))
    items += [('fragment_leaf', {'fragment_id': f, 'revision': r, 'ord': o}, frm, {}) for f,r,o in cur.fetchall()]
    for seq, (tbl, key, old, guard) in enumerate(items):
        cur.execute('''insert into knowledge.subject_merge_item(merge_id,seq,tbl,row_key,old_subject,new_subject,guard)
            values(%s,%s,%s,%s,%s,%s,%s)''', (mid, seq, tbl, json.dumps(key), old, into, json.dumps(guard)))
    cur.execute('update knowledge.subject_alias set subject_id=%s,merged_by=%s where host_id=%s and subject_id=%s', (into, mid, host, frm))
    cur.execute('update knowledge.subject set parent_subject_id=%s where parent_subject_id=%s',(into,frm))
    cur.execute('update knowledge.subject set merged_into=%s where subject_id=%s or merged_into=%s', (into, frm, frm))
    cur.execute('update knowledge.fragment set subject_id=%s where host_id=%s and subject_id=%s', (into, host, frm))
    cur.execute('update knowledge.fragment_leaf set subject_id=%s where subject_id=%s', (into, frm))
    gen = _bump(cur, host, domain=row[0])
    queue_rescan(cur, host, [into], gen, {'merge': mid})
    cur.execute("update knowledge.subject_merge set state='done',generation_after=%s where merge_id=%s", (gen, mid))
    return {**result, 'status': 'merged', 'merge_id': mid}


def _current(cur, host, tbl, key):
    """(found, subject value, guard of the row now) -- compared with what the merge logged."""
    if tbl == "subject_alias":
        cur.execute("""select subject_id::text, kind, source_fragment_id::text, source_revision, merged_by::text
                       from knowledge.subject_alias where host_id=%s and alias=%s and domain=%s for update""", (host, key["alias"], key["domain"]))
        row = cur.fetchone()
        return (True, row[0], {"kind": row[1], "source_fragment_id": row[2], "source_revision": row[3]}, row[4]) if row \
            else (False, None, None, None)
    if tbl == "subject":
        field = 'parent_subject_id' if key.get('field') == 'parent_subject_id' else 'merged_into'
        cur.execute(f"select {field}::text from knowledge.subject where subject_id=%s for update", (key["subject_id"],))
        row = cur.fetchone()
        return (True, row[0], {}, None) if row else (False, None, None, None)
    if tbl == "fragment":
        # astra merge r2 P1-1: structure filled after the merge (same revision, new leaves) also refuses the undo
        cur.execute("select subject_id::text, revision, form is not null from knowledge.fragment where id=%s for update",
                    (key["id"],))
        row = cur.fetchone()
        return (True, row[0], {"revision": row[1], "structured": row[2]}, None) if row else (False, None, None, None)
    cur.execute("""select subject_id::text from knowledge.fragment_leaf where fragment_id=%s and revision=%s and ord=%s
                   for update""", (key["fragment_id"], key["revision"], key["ord"]))
    row = cur.fetchone()
    return (True, row[0], {}, None) if row else (False, None, None, None)


def _set(cur, host, tbl, key, value):
    if tbl == "subject_alias":
        cur.execute("update knowledge.subject_alias set subject_id=%s, merged_by=%s where host_id=%s and alias=%s and domain=%s",
                    (value, key.get("merged_by_before"), host, key["alias"], key["domain"]))
    elif tbl == "subject":
        field = 'parent_subject_id' if key.get('field') == 'parent_subject_id' else 'merged_into'
        cur.execute(f"update knowledge.subject set {field}=%s where subject_id=%s", (value, key["subject_id"]))
    elif tbl == "fragment":
        cur.execute("update knowledge.fragment set subject_id=%s where id=%s", (value, key["id"]))
    else:
        cur.execute("update knowledge.fragment_leaf set subject_id=%s where fragment_id=%s and revision=%s and ord=%s",
                    (value, key["fragment_id"], key["revision"], key["ord"]))


from contextlib import contextmanager


@contextmanager
def _undo_cursor(dsn, cur):
    if cur is not None:
        yield cur
    else:
        with pg.connect(dsn) as conn, conn.cursor() as own:
            yield own


def undo_merge(dsn, host, merge_id, quote, locator=None, *, _cur=None):
    """Revert exactly the rows one merge moved, after the user says the merge was wrong. Later merges touching either
    subject (by generation, issued under the host lock) are undone first; a logged row changed since the merge -- its
    subject, a fragment's revision, an alias's kind or evidence -- refuses the undo (nothing is reverted)."""
    if not _norm(quote):
        raise ValueError("user_quote is required")
    with _undo_cursor(dsn, _cur) as cur:
        lock_host(cur, host, exclusive=True)
        cur.execute("""select from_subject::text, into_subject::text, state, created_at, generation_after
                       from knowledge.subject_merge where merge_id=%s and host_id=%s for update""", (merge_id, host))
        m = cur.fetchone()
        if not m:
            raise ValueError("merge not found for host")
        frm, into, state, created, gen_after = m
        if state != "done":
            raise ValueError(f"merge is {state}; only a done merge can be undone")
        cur.execute('''select new_subject::text from knowledge.g16_subject_map
                       where old_subject=any(%s::uuid[])''', ([frm, into],))
        subject_ids = sorted({frm, into, *(r[0] for r in cur.fetchall())})
        cur.execute("""select merge_id::text from knowledge.subject_merge where host_id=%s and state <> 'undone'
                       and merge_id <> %s and generation_after > %s
                       and (from_subject = any(%s::uuid[]) or into_subject = any(%s::uuid[]))
                       order by generation_after desc""", (host, merge_id, gen_after, subject_ids, subject_ids))
        later = [x[0] for x in cur.fetchall()]
        if later:
            raise ValueError(f"undo the later merges of these subjects first: {later}")
        cur.execute("""select seq, tbl, row_key, old_subject::text, new_subject::text, guard from knowledge.subject_merge_item
                       where merge_id=%s order by seq desc""", (merge_id,))
        items = cur.fetchall()
        cur.execute('''select seq,tbl,row_key,old_subject::text,new_subject::text,guard
                       from knowledge.g16_merge_item where merge_id=%s order by seq desc,domain''', (merge_id,))
        projected = cur.fetchall()
        if projected:
            items = projected
        cur.execute('select domain from knowledge.subject where subject_id=%s', (frm,))
        legacy_domain = cur.fetchone()[0]
        for _, tbl, key, _, _, _ in items:
            if tbl == 'subject_alias':
                key.setdefault('domain', legacy_domain)
        changed = []
        for seq, tbl, key, old, new, guard in items:
            found, value, now_guard, merged_by = _current(cur, host, tbl, key)
            if (not found or value != new or (guard and now_guard != guard)
                    or (tbl == "subject_alias" and merged_by != merge_id)):
                changed.append({"tbl": tbl, "row": key, "now": value})
        if changed:
            raise ValueError(f"rows moved by this merge changed since; nothing was undone: {changed[:5]}")
        # the undo transaction becomes the one allowed to move the logged rows back
        cur.execute("update knowledge.subject_merge set state='undoing', txid=txid_current() where merge_id=%s", (merge_id,))
        cur.execute("select set_config('knowledge.merge_id', %s, true)", (merge_id,))
        for seq, tbl, key, old, new, guard in items:
            _set(cur, host, tbl, key, old)
        moved = {key["id"] for _, tbl, key, _, _, _ in items if tbl == "fragment"}
        cur.execute('''select new_subject::text from knowledge.g16_subject_map where old_subject=%s''', (into,))
        survivors = sorted({into, *(r[0] for r in cur.fetchall())})
        cur.execute("""select alias from knowledge.fragment where host_id=%s and subject_id=any(%s::uuid[]) and created_at > %s""",
                    (host, survivors, created))
        kept = [x[0] for x in cur.fetchall()]  # recorded after the merge: their original subject is unknown
        gen = _bump(cur, host, domain_of(cur, frm))
        queued = queue_rescan(cur, host, subject_ids, gen, {"undo": merge_id})
        cur.execute("""update knowledge.subject_merge set state='undone', undo_quote=%s, undone_at=now()
                       where merge_id=%s""", (quote, merge_id))
        return {"merge_id": merge_id, "undone": True, "reverted_rows": len(items), "fragments_back": len(moved),
                "kept_on_survivor": kept, "rescan": queued,
                "next": ("Undone. Fragments recorded after the merge stay with the surviving subject: "
                         f"{kept}. Tell the user; fix them with knowledge_record update if they belong to the other one.")
                if kept else "Undone."}
