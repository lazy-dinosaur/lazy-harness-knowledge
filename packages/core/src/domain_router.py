"""Domain routing for digestion (schema-delta: domain tags, routing decided 2026-09-27).

The worker writes a rough domain name in partition_key. Digestion routes each add:
  1. proposed name is an active domain (after following merges) -> use it, no Jev call
  2. no active domains yet -> create the proposed name
  3. otherwise Jev choice over active domains (+ 'none'):
       top is an existing domain            -> that domain
       top is 'none' and p(none) >= NEW_MIN -> create the proposed name
       ambiguous                            -> the best existing domain (avoid scattering names)
Jev cannot write names, so a new domain always takes the worker's proposed name.
"""
import json
import re
from candidate_policy import top_k

NEW_MIN = 0.7
ROUTE_MIN = 0.6  # route_unit: join an existing domain only when Jev is at least this sure; otherwise create
SAME_MIN = 0.8  # G5: before creating, a one-to-one 'same area?' check against the closest existing domains
CONFIRM_TOP = 2
SAME_Q = ("items[{i}] 는 이미 있는 영역(도메인)이다. facts 의 작업이 속한 영역과 같은 영역인가? "
          "기능이 달라도 같은 업무 영역이면 예. 다른 업무 영역이면 아니오.")


def norm(name):
    return re.sub(r"\s+", " ", (name or "").strip())


def load(cur, host):
    """{domain: row} for the host, rows have description/status/merged_into."""
    cur.execute("select domain, description, status, merged_into from knowledge.domain_type where host_id=%s", (host,))
    return {r[0]: {"description": r[1], "status": r[2], "merged_into": r[3]} for r in cur.fetchall()}


def resolve(domains, name):
    """Follow merged_into to the surviving domain (cycle-safe)."""
    seen = set()
    while name in domains and domains[name]["merged_into"] and name not in seen:
        seen.add(name)
        name = domains[name]["merged_into"]
    return name


def route(domains, proposed, fact_text, choose):
    """Return (domain, how). choose(fact_text, options:{key: description}) -> {key: probability}."""
    proposed = norm(proposed)
    active = {d: v for d, v in domains.items() if v["status"] == "active"}
    if proposed:
        r = resolve(domains, proposed)
        if r in active:
            return r, "exact"
    if not active:
        return proposed, "new"
    names = sorted(active)
    options = {f"d{i}": f"{n}: {active[n]['description'] or n}" for i, n in enumerate(names)}
    options["none"] = "위 어느 영역에도 속하지 않는다(새 영역)"
    probs = choose(fact_text, options)
    best = max((k for k in probs if k != "none"), key=lambda k: probs.get(k, 0.0), default=None)
    top = max(probs, key=probs.get) if probs else None
    if top == "none" and probs["none"] >= NEW_MIN and proposed:
        return proposed, "new"
    if best is not None:
        return names[int(best[1:])], "routed"
    return (proposed or names[0]), "new" if proposed else "routed"


def document_text(facts, budget=12000):
    # Every fact contributes, including the last; do not take only the first eight.
    if not facts: return ''
    if len(facts)*4 > budget: raise ValueError('too many facts for bounded document routing')
    width=max(1, (budget-len(facts)*3)//len(facts))
    return '\n'.join('- '+str(f)[:width] for f in facts)


def route_unit(domains, proposed, facts, choose, confirm=None, name_area=None):
    """Route a whole work unit (all its add facts) to ONE domain. Return (domain, how).
    A fragment has exactly one domain (2026-09-28: related-domain lists were tried and dropped — auto links were
    39% wrong and cross-domain knowledge was 0/42; knowledge shared by two domains is handled with merge).
    Area rebuild uses partition_key only as a hint. A confident none can ask Luna
    for an area-level name; ambiguity never creates a document-sized domain."""
    proposed = norm(proposed)
    active = {d: v for d, v in domains.items() if v["status"] == "active"}
    if proposed and name_area is None:
        r = resolve(domains, proposed)
        if r in active:
            return r, 'exact'
    text = document_text(facts)
    if not active and name_area is None:
        return (proposed or 'general'), 'new'
    names = sorted(active)
    options = {f"d{i}": f"{n}: {active[n]['description'] or n}" for i, n in enumerate(names)}
    options["none"] = "위 어느 영역에도 속하지 않는다(새 영역)"
    probs = choose(text, options)
    top = max(probs, key=probs.get) if probs else None
    if top and top != "none" and probs[top] >= ROUTE_MIN:
        return names[int(top[1:])], "routed"
    if top == 'none' and probs[top] >= NEW_MIN:
        named=name_area(text, proposed) if name_area else proposed or 'general'
        if isinstance(named,dict):
            name=norm(named['name'])
            domains[name]={'description':named['description'],'status':'active','merged_into':None}
        else: name=norm(named)
        if not name: raise ValueError('empty area name')
        return name, 'new'
    # G5 (user 2026-09-28): stop scattering at creation — before making a new domain, ask one-to-one about the
    # closest existing ones (a pairwise question is sharper than the multi-choice); confirm(text, [option text]) -> scores.
    if confirm is not None:
        near = sorted((k for k in probs if k != "none"), key=lambda k: -probs[k])[:CONFIRM_TOP]
        if near:
            scores = confirm(text, [options[k] for k in near])
            best = max(range(len(near)), key=lambda i: scores[i])
            if scores[best] >= SAME_MIN:
                return names[int(near[best][1:])], "confirmed"
    if names:
        best=max((k for k in options if k != 'none'), key=lambda k: probs.get(k,0))
        return names[int(best[1:])], 'ambiguous-existing'
    raise ValueError('new area requires confident none')


def route_pending(dsn, unit_id, choose=None, confirm=None, embed_fn=None, name_area=None):
    """Router runs BEFORE actors. Persist one immutable assignment per ledger fact.

    Updates belong to the target's partition; adds are routed once per ledger entry (its declared domain or
    entry partition key). A completed cross-domain unit is not a global transaction.
    """
    import store_pg as pg
    import subject_dict
    with pg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select host_id,status::text from knowledge.work_unit where work_unit_id=%s", (unit_id,))
        unit = cur.fetchone()
        if not unit or unit[1] != 'completed':
            return []
        host = unit[0]
        domains = load(cur, host)
        cur.execute('select alias,domain from knowledge.domain_alias where host_id=%s', (host,))
        aliases = dict(cur.fetchall())
        cur.execute("""select e.entry_id::text,e.partition_key,e.judgement_body
            from knowledge.ledger_entry e where e.work_unit_id=%s
            and e.state not in ('closed','expired','rejected_input')
            order by e.created_at,e.entry_id""", (unit_id,))
        entries = cur.fetchall()
        cur.execute("""select p.entry_id::text,p.fact_index from knowledge.digest_part p
            join knowledge.ledger_entry e using(entry_id) where e.work_unit_id=%s""", (unit_id,))
        assigned = set(cur.fetchall())
        targets = {}
        for eid, key, body in entries:
            for i, fact in enumerate(body['facts']):
                if fact.get('operation') in ('update', 'deprecate'):
                    cur.execute('select domain from knowledge.fragment where host_id=%s and id=%s',
                                (host, fact.get('target_ref')))
                    row = cur.fetchone()
                    if not row:
                        raise ValueError('partition target missing for host')
                    targets[eid, i] = row[0]
    import embed
    if embed_fn is embed.encode_queries:
        from embedding_inputs import warm
        try:
            warm([fact for _, _, body in entries for fact in body['facts']])
        except embed.EmbeddingUnavailable:
            pass
    assignments = []
    original_domains=set(domains)
    for eid, key, body in entries:
        # User decision 2026-09-27 (schema-delta '작업 단위 도메인 라우팅', Task339 restored): one routing decision per
        # ledger entry, from that entry's own facts -- not per fact, and not one decision for the whole work unit.
        routed_domain = None
        additions = [fact.get('fact', '') for i, fact in enumerate(body['facts']) if (eid, i) not in targets]
        for i, fact in enumerate(body['facts']):
            if (eid, i) in assigned:
                continue
            if fact.get('identity_ref') is not None:
                from identity_ref import resolve as resolve_ref
                with pg.connect(dsn) as conn, conn.cursor() as cur:
                    obj = resolve_ref(cur,fact['identity_ref'],host,fact['identity_ref']['domain'])
                domain = obj['domain']
                if (eid,i) in targets and targets[eid,i] != domain:
                    raise ValueError('E_IDENTITY: target fragment domain drift')
            elif (eid, i) in targets:
                domain = resolve(domains, targets[eid, i])
            else:
                if routed_domain is None:
                    proposed = norm(fact.get('domain') or key)
                    proposed = aliases.get(proposed, proposed) if name_area is None else proposed
                    candidates = domains
                    if name_area is not None or resolve(domains, proposed) not in {d for d,row in domains.items() if row['status']=='active'}:
                        candidates = {}
                        if domains and (embed_fn or choose or confirm):
                            vector = (embed_fn or embed.encode_queries)([document_text(additions)])[0]
                            with pg.connect(dsn) as conn, conn.cursor() as cur:
                                near = nearest(cur, host, vector)
                            candidates = {d: domains[d] for d in near}
                    routed_domain, _ = route_unit(candidates, proposed, additions,
                        choose or (lambda text, options: {k: 0.0 for k in options}), confirm, name_area)
                    domains.update(candidates)
                domain = routed_domain
            domains.setdefault(domain, {'description': fact.get('fact', '')[:300],
                                        'status': 'active', 'merged_into': None})
            assignments.append((eid, i, domain, domains[domain]['description'],
                                norm(fact.get('domain') or key) if (eid,i) not in targets and not fact.get('identity_ref') else None))
    new_vectors={}
    if name_area is not None:
        new_names=sorted(set(domains)-original_domains)
        if new_names:
            new_vectors=dict(zip(new_names,(embed_fn or embed.encode_queries)([subject_dict.identity_text(d,domains[d]['description']) for d in new_names])))
    with pg.connect(dsn) as conn, conn.cursor() as cur:
        # Only router metadata is serialized, never the partition actors.
        cur.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))', ('lh-router/' + host,))
        current = load(cur, host)
        for eid, i, domain, description, alias in assignments:
            fixed = next(body['facts'][i].get('identity_ref') for entry_id,_,body in entries if entry_id==eid)
            if fixed is not None:
                from identity_ref import resolve as resolve_ref
                resolve_ref(cur,fixed,host,domain)
            else:
                domain = resolve(current, domain)
            ensure(cur, host, domain, description)
            if domain in new_vectors:
                import subject_dict
                cur.execute('''insert into knowledge.domain_embedding(host_id,domain,model_id,embedding)
                    values(%s,%s,%s,%s::extensions.vector) on conflict do nothing''',
                    (host,domain,embed.MODEL_ID,subject_dict._vec(new_vectors.pop(domain))))
            if alias and alias != domain:
                cur.execute('''insert into knowledge.domain_alias(host_id,alias,domain,evidence)
                    values(%s,%s,%s,%s) on conflict do nothing''',
                    (host,alias,domain,json.dumps([{'entry_id':eid,'fact_index':i}])))
            cur.execute('''insert into knowledge.digest_part(entry_id,fact_index,host_id,domain)
                values(%s,%s,%s,%s) on conflict do nothing''', (eid, i, host, domain))
        cur.execute('''select distinct p.domain from knowledge.digest_part p
            join knowledge.ledger_entry e using(entry_id) where e.work_unit_id=%s
            and not exists(select 1 from knowledge.absorption a where a.entry_id=p.entry_id
                and a.fact_index=p.fact_index and a.decision::text in ('absorbed','retained_as_evidence','rejected'))
            order by p.domain''', (unit_id,))
        return [(host, d[0]) for d in cur.fetchall()]


def nearest(cur, host, vector, limit=None):
    from embedding_guard import assert_host
    limit = top_k(limit)
    assert_host(cur, host)
    import embed
    import subject_dict
    cur.execute('''select d.domain,1-(e.embedding operator(extensions.<=>) %s::extensions.vector) as similarity
        from knowledge.domain_embedding e join knowledge.domain_type d using(host_id,domain)
        where e.host_id=%s and e.model_id=%s and d.status='active'
        order by similarity desc,d.domain limit %s''', (subject_dict._vec(vector),host,embed.MODEL_ID,limit))
    return [d for d, score in cur.fetchall()]


def maintain_domains(dsn, host, embed_fn, merge_luna, merge_same):
    """Coordinator runs AFTER partition workers join; never merges from inside an actor."""
    import store_pg as pg
    import subject_dict
    outcomes, seen = [], set()
    with pg.connect(dsn) as conn, conn.cursor() as cur:
        domains = load(cur,host)
    for domain, row in sorted(domains.items()):
        if row['status'] != 'active':
            continue
        vector = embed_fn([subject_dict.identity_text(domain,row['description'])])[0]
        with pg.connect(dsn) as conn, conn.cursor() as cur:
            candidates = nearest(cur,host,vector)
        for other in candidates:
            pair = tuple(sorted((domain,other)))
            if domain == other or pair in seen:
                continue
            seen.add(pair)
            with pg.connect(dsn) as conn, conn.cursor() as cur:
                current = load(cur,host)
                if any(current[d]['status'] != 'active' for d in pair):
                    continue
                outcomes.append(automatic_merge(cur,host,pair[1],pair[0],merge_luna,merge_same))
    return outcomes


def lock_partition(cur, host, domain):
    """Transaction writer lock; unrelated domain commits never take the host lock."""
    cur.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))',
                (f'lh-partition/{host}/{domain}',))


def pending_parts(cur, host, domain):
    """Read projection of confirmed but unapplied bodies, not canonical fragments."""
    cur.execute('''select p.entry_id::text,p.fact_index,e.judgement_body->'facts'->p.fact_index
        from knowledge.digest_part p join knowledge.ledger_entry e using(entry_id)
        join knowledge.work_unit w using(work_unit_id)
        where p.host_id=%s and p.domain=%s and w.status='completed'
        and not exists(select 1 from knowledge.absorption a where a.entry_id=p.entry_id
            and a.fact_index=p.fact_index and a.decision::text in ('absorbed','retained_as_evidence','rejected'))
        order by e.created_at,p.fact_index''', (host, domain))
    return [{'entry_id': e, 'fact_index': i, 'text': f.get('fact', ''),
             'domain': domain, 'status': '반영 대기'} for e, i, f in cur.fetchall()]


def make_confirm(cfg):
    """Jev one-to-one 'same area?' check for route_unit (noul per candidate domain)."""
    import worker_tools
    ask = worker_tools.make_ask(cfg)
    return lambda facts_text, candidates: ask({"facts": facts_text}, candidates, SAME_Q)


def ensure(cur, host, domain, description):
    from subject_contract import short_name_errors
    errors = short_name_errors(domain, area=True)
    if errors:
        cur.execute('select 1 from knowledge.domain_type where host_id=%s and domain=%s',(host,domain))
        if not cur.fetchone(): raise ValueError(str(errors))
    cur.execute("""insert into knowledge.domain_type(host_id, domain, description) values (%s,%s,%s)
                on conflict (host_id, domain) do nothing""", (host, domain, (description or "")[:300]))



def source_review_catalog(cur,host):
    """Actual supported catalog read: no saved request catalog as creation authority."""
    live=load(cur,host)
    cur.execute('select alias,domain from knowledge.domain_alias where host_id=%s',(host,));aliases=cur.fetchall()
    cur.execute('select domain,description_evidence from knowledge.domain_type where host_id=%s',(host,));evidence=dict(cur.fetchall())
    result=[]
    for name,row in sorted(live.items()):
        if row['status']!='active' or row['merged_into']:continue
        entry={'name':name,'description':row['description'],'aliases':sorted(a for a,owner in aliases if owner==name)}
        organizing=[v for v in evidence[name] if v.get('type')=='source-organizing-area/v1']
        if organizing:entry['organizing_scope']=organizing
        result.append(entry)
    return result

def ensure_reviewed(cur, host, domain, receipt):
    """Materialize ONLY a sealed new-area decision; never retype existing catalog rows."""
    import source_role_contract as contract
    import domain_source_review as review
    import json
    if not contract.verified(receipt) or receipt.get('host_semantic_policy') not in (review.SHARED_POLICY,review.TECHNICAL_POLICY):
        raise ValueError('sealed explicit organizing-area policy required')
    data=contract.strict_json(receipt['request']['context']['messages'][0]['content'])['data']
    chosen,new=review.consume(receipt,data['unit'],data['source'],data['catalog'])
    if not new or data['host_id']!=host or chosen['name']!=domain['name'] or chosen['description']!=domain['description'] or chosen.get('area_evidence')!=domain.get('area_evidence'):
        raise ValueError('new-area materialization binding drift')
    cur.execute('select description,description_evidence from knowledge.domain_type where host_id=%s and domain=%s for update',(host,domain['name']))
    existing=cur.fetchone()
    evidence={'type':'source-organizing-area/v1',**chosen['area_evidence']}
    if existing:
        if existing[0]!=domain['description'] or evidence not in existing[1]:
            raise ValueError('existing area ownership/scope must not be retyped')
        return
    from subject_contract import short_name_errors
    if short_name_errors(domain['name'],area=True):raise ValueError('invalid organizing area name')
    review.consume(receipt,data['unit'],data['source'],source_review_catalog(cur,host))
    cur.execute('''insert into knowledge.domain_type(host_id,domain,description,description_evidence,description_updated_at)
        values(%s,%s,%s,%s::jsonb,now())''',(host,domain['name'],domain['description'],json.dumps([evidence],ensure_ascii=False)))

def make_choose(cfg, http=None):
    """Jev choice judge: which domain does this fact belong to."""
    import capture_audit
    judge = capture_audit.make_judge(cfg, **({"http": http} if http else {}))
    def choose(fact_text, options):
        a = judge({"document": fact_text}, ['이 문서'], "이 문서의 지식은 어느 업무 영역에 속하는가? 기능이 달라도 같은 업무 영역이면 그 영역. items[{i}]", options)[0]
        p = a.get("probabilities") or {}
        return {k: float(p.get(k, 1.0 if a.get("choice") == k else 0.0)) for k in options}
    return choose


# --- management (knowledge_cli domain) ---
def list_domains(cur, host):
    ds = load(cur, host)
    cur.execute("select domain, count(*) from knowledge.fragment where host_id=%s and active group by domain", (host,))
    counts = dict(cur.fetchall())
    return [{"domain": d, **v, "fragments": counts.get(d, 0)} for d, v in sorted(ds.items())]


def fragments(cur, host, domain):
    """Active fragments of `domain` and of every domain merged into it."""
    ds = load(cur, host)
    target = resolve(ds, domain)
    names = [d for d in ds if resolve(ds, d) == target] or [target]
    cur.execute("""select id::text, alias, domain, text from knowledge.fragment
                   where host_id=%s and active and domain = any(%s)
                   order by domain, seq""", (host, names))
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()] + pending_parts(cur, host, target)


def merge(cur, host, src, dst, actor='user', reason=None, evidence=None, *, luna=None, same=None):
    import subject_merge
    subject_merge.lock_host(cur, host, exclusive=False)
    cur.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))', ('lh-router/' + host,))
    for domain in sorted((src, dst)):
        cur.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))', (f'lh-actor/{host}/{domain}',))
        lock_partition(cur, host, domain)
    ds = load(cur, host)
    if src not in ds or dst not in ds or src == dst:
        raise ValueError("unknown or identical domain")
    if resolve(ds, dst) == src:
        raise ValueError("merge would create a cycle")
    cur.execute('select to_jsonb(d) from knowledge.domain_type d where host_id=%s and domain=%s for update', (host, src))
    before = cur.fetchone()[0]
    cur.execute("""update knowledge.domain_type set status='retired', merged_into=%s, updated_at=now()
                where host_id=%s and domain=%s returning to_jsonb(domain_type)""", (dst, host, src))
    after = cur.fetchone()[0]
    if actor == 'luna-auto' and (not reason or not evidence):
        raise ValueError('automatic domain merge requires reason and evidence')
    cur.execute('''insert into knowledge.domain_merge(host_id,from_domain,into_domain,actor,reason,evidence,before_row,after_row)
        values(%s,%s,%s,%s,%s,%s,%s,%s) returning merge_id::text''',
        (host, src, dst, actor, json.dumps(reason or {'kind': 'explicit-domain-merge'}),
         json.dumps(evidence or [{'source': 'domain merge command', 'from': src, 'into': dst}]),
         json.dumps(before), json.dumps(after)))
    mid = cur.fetchone()[0]
    _move_domain(cur, host, src, dst, mid, luna, same)
    return mid


def undo_merge(cur, host, merge_id, reason):
    cur.execute('select 1 from knowledge.domain_merge where merge_id=%s and host_id=%s', (merge_id, host))
    if not cur.fetchone():
        raise ValueError('domain merge not found for host')
    _undo_domain_move(cur, host, merge_id, reason)
    cur.execute('select knowledge.undo_domain_merge(%s,%s)', (merge_id, reason))


def _move_domain(cur, host, src, dst, mid, luna, same):
    """Rare, deliberately simple structural relocation under both partition locks."""
    import subject_merge
    cur.execute("select set_config('knowledge.domain_move_id',%s,true)", (mid,))
    cur.execute('select name,subject_id::text from knowledge.subject where host_id=%s and domain=%s', (host, dst))
    names = dict(cur.fetchall())
    cur.execute('select alias from knowledge.subject_alias where host_id=%s and domain=%s', (host, dst))
    aliases = {r[0] for r in cur.fetchall()}
    cur.execute('select coalesce(max(seq),0) from knowledge.fragment where host_id=%s and domain=%s', (host, dst))
    seq = cur.fetchone()[0]
    collisions, moves = [], []
    keys = {'subject': ['subject_id'], 'subject_alias': ['host_id','domain','alias'],
            'fragment': ['id'], 'digest_part': ['entry_id','fact_index']}
    for table in keys:
        cur.execute(f'select to_jsonb(t) from knowledge.{table} t where host_id=%s and domain=%s', (host, src))
        for (before,) in cur.fetchall():
            after = {**before, 'domain': dst}
            if table == 'subject' and before['name'] in names:
                collisions.append((before['subject_id'], names[before['name']]))
                after['name'] += '@g16:' + before['subject_id']
            if table == 'subject_alias' and before['alias'] in aliases:
                after['alias'] += '@g16:' + before['subject_id']
            if table == 'fragment':
                seq += 1
                after['seq'] = seq
            key = {k: before[k] for k in keys[table]}
            moves.append((table, key, before, after))
    for n, (table, key, before, after) in enumerate(moves):
        cur.execute('''insert into knowledge.domain_move(merge_id,seq,tbl,row_key,before_row,after_row)
            values(%s,%s,%s,%s,%s,%s)''', (mid,n,table,json.dumps(key),json.dumps(before),json.dumps(after)))
        _set_move(cur, table, before, after)
    for frm, into in collisions:
        # Missing semantic adapters are unknown, never implicit permission to merge.
        outcome = subject_merge.automatic_merge(cur, host, frm, into,
            luna or (lambda _: {'relation':'unknown'}), same or (lambda _: 0.0))
        if outcome['status'] == 'merged':
            cur.execute('insert into knowledge.domain_subject_merge values(%s,%s)', (mid, outcome['merge_id']))


def _set_move(cur, table, before, after):
    allowed = {'subject': ('subject_id',), 'fragment': ('id',),
               'subject_alias': ('host_id','domain','alias'), 'digest_part': ('entry_id','fact_index')}
    if table not in allowed:
        raise ValueError('unknown move table')
    changes = [k for k in ('domain','name','alias','seq') if k in after and before.get(k) != after[k]]
    if changes:
        where = ' and '.join(k+'=%s' for k in allowed[table])
        cur.execute(f"update knowledge.{table} set " + ','.join(k+'=%s' for k in changes) + ' where '+where,
                    tuple(after[k] for k in changes) + tuple(before[k] for k in allowed[table]))
        if cur.rowcount != 1:
            raise ValueError('domain move row disappeared')


def _undo_domain_move(cur, host, mid, reason):
    import subject_merge
    cur.execute('select from_domain,into_domain from knowledge.domain_merge where host_id=%s and merge_id=%s', (host,mid))
    src, dst = cur.fetchone()
    for domain in sorted((src,dst)):
        cur.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))', (f'lh-actor/{host}/{domain}',))
        lock_partition(cur, host, domain)
    subject_merge.lock_host(cur, host, exclusive=True)
    cur.execute('''select s.subject_merge_id::text from knowledge.domain_subject_merge s
        join knowledge.subject_merge m on m.merge_id=s.subject_merge_id
        where s.domain_merge_id=%s order by m.generation_after desc''', (mid,))
    for (sid,) in cur.fetchall():
        subject_merge.undo_merge(None, host, sid, reason, _cur=cur)
    cur.execute('select tbl,before_row,after_row from knowledge.domain_move where merge_id=%s order by seq', (mid,))
    moves = cur.fetchall()
    # Validate every snapshot before any reverse move (transaction rollback also covers linked subject undo).
    keys = {'subject': ('subject_id',), 'fragment': ('id',),
            'subject_alias': ('host_id','domain','alias'), 'digest_part': ('entry_id','fact_index')}
    for table, before, after in moves:
        where = ' and '.join(k+'=%s' for k in keys[table])
        cur.execute(f'select to_jsonb(t) from knowledge.{table} t where '+where, tuple(after[k] for k in keys[table]))
        row = cur.fetchone()
        if not row or row[0] != after:
            raise ValueError('domain move row changed after merge; nothing undone')
    cur.execute("select set_config('knowledge.domain_move_id',%s,true),set_config('knowledge.domain_move_undo','on',true)", (mid,))
    for table, before, after in moves:
        _set_move(cur, table, after, before)


def automatic_merge(cur, host, src, dst, luna, same):
    """Evidence-gated real domain move; ambiguity is held, never a redirect-only merge."""
    import subject_merge
    subject_merge.lock_host(cur,host,exclusive=False)
    cur.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))', ('lh-router/' + host,))
    for domain in sorted((src,dst)):
        cur.execute('select pg_advisory_xact_lock(hashtextextended(%s,0))', (f'lh-actor/{host}/{domain}',))
        lock_partition(cur,host,domain)
    ds = load(cur, host)
    if src not in ds or dst not in ds or any(ds[d]['status'] != 'active' for d in (src,dst)):
        raise ValueError('active domain not found')
    cur.execute('select id::text,revision,text,source from knowledge.fragment where host_id=%s and domain=any(%s) order by id',
                (host,[src,dst]))
    snapshot = {'domains': {d: ds[d] for d in (src,dst)}, 'fragments': cur.fetchall()}
    verdict = subject_merge.judge_merge(cur, host, 'domain', [src,dst], snapshot, luna, same)
    if verdict['status'] != 'same':
        return verdict
    mid = merge(cur,host,src,dst,'luna-auto',verdict['reason'],verdict['evidence'],luna=luna,same=same)
    return {**verdict,'status':'merged','merge_id':mid}


def describe(cur, host, domain, description):
    """Update only an existing active domain; never follow merges or reclassify fragments."""
    if not isinstance(domain, str) or not domain.strip():
        raise ValueError("domain must be a non-empty string")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description must be a non-empty string")
    domain, description = domain.strip(), description.strip()
    if len(description) > 300:
        raise ValueError("description must be at most 300 characters")
    cur.execute("""update knowledge.domain_type set description=%s, updated_at=now()
                where host_id=%s and domain=%s and status='active'""", (description, host, domain))
    if cur.rowcount != 1:
        raise ValueError("unknown or inactive domain")


def retire(cur, host, domain):
    cur.execute("""update knowledge.domain_type set status='retired', updated_at=now()
                where host_id=%s and domain=%s and merged_into is null""", (host, domain))
    if cur.rowcount != 1:
        raise ValueError("unknown domain")
