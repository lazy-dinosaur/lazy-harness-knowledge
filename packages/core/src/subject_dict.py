"""Subject dictionary (schema-delta '주어는 기록할 때 맞추고 소화할 때 등록', 2026-09-30).

One dictionary per (host, domain). Record time only reads it: an existing subject replaces the written name in the fact.
Digestion is the only writer: a name still new is re-checked against subjects registered after the record was made,
then registered. Matching (structure-01 contra04, 2026-09-30): code first strips generic tails (화면/기능/페이지/함수/
동작/창) and matches exactly; otherwise top-K vector candidates on that core go to Jev with each subject's sentences as JSON
(name-only Jev: variants 4/8; tail + sentences: 8/8, document/test traps merged 0/6 in both).
"""
import json
import math
import re

import embed
from candidate_policy import top_k

SAME_KEEP = 0.9  # Jev same-entity probability to reuse an existing name (flow3 r2: 0.7 merged 'domain describe' into '도메인 설명')
# generic tails that do not change the entity; 테스트/SDD/문서/규칙 are NOT here (they name a different thing)
TAIL = re.compile(r"(?:\s*(?:화면|기능|페이지|함수|동작|창))+$")
SAME_Q = ("state.new 와 items[{i}] 는 지식 문장의 주어 두 개다. 각각 name 과 그 주어가 쓰인 문장(sentences)이 있다. "
          "문장 내용을 보고 두 name 이 같은 대상을 가리키는지 판단하라. "
          "true: 표기·띄어쓰기·영문/한글·백틱 차이, 같은 대상에 붙인 '기능/화면/페이지/동작/함수' 같은 수식어 차이. "
          "false: 서로 다른 대상(형제 기능, 다른 파일, 다른 화면), 그 대상에 관한 문서·SDD·규칙·계약·테스트·변경·경로.")
NEW = ("new", "new_unchecked", "pending")


def norm(name):
    s = re.sub(r"[`'\"]", "", str(name or "")).strip()
    s = re.sub(r"\s+", " ", s)
    return s.lower() if s.isascii() else s


def core(name):
    n = norm(name)
    c = TAIL.sub("", n).strip() or n
    return re.sub(r"[A-Z]+", lambda m: m.group(0).lower(), c)  # latin letters case-insensitive: 'CALL 화면' == 'CALL'


def _vec(values):
    return "[" + ",".join(str(float(v)) for v in values) + "]"


def cos(u, v):
    d = math.sqrt(sum(a * a for a in u)) * math.sqrt(sum(b * b for b in v))
    return sum(a * b for a, b in zip(u, v)) / d if d else 0.0


def lookup(cur, host, name, domain=None, *, exact_only=False):
    """Known spelling; legacy callers also try its core without a generic tail."""
    keys = (norm(name),) if exact_only else (norm(name), core(name))
    for key in dict.fromkeys(keys):
        # a declared alias matches its exact spelling only; tail stripping (화면/기능/…) is for spelling aliases (astra alias r1)
        # stage 2: a name moved by a subject merge reads as 'merged' -- same subject, the text keeps the written name
        cur.execute("""select s.subject_id::text, s.name,
                       case when a.merged_by is not null and a.kind = 'spelling' then 'merged' else a.kind end, s.subject_type, s.description
                       from knowledge.subject_alias a
                       join knowledge.subject s on s.subject_id=a.subject_id and s.host_id=a.host_id and s.domain=a.domain
                       where a.host_id=%s and a.alias=%s and (%s or a.kind = 'spelling')
                       and (%s::text is null or a.domain=%s)""",
                    (host, key, key == norm(name), domain, domain))
        matches = cur.fetchall()
        row = matches[0] if len(matches) == 1 else None
        if row:
            return {"subject_id": row[0], "name": row[1], "kind": row[2], "subject_type": row[3], "description": row[4]}
    return None


def _lock_name(cur, host, name, domain=None):
    """Locks for every dictionary key a writer reads or inserts -- the spelling and its core ('X 화면' shares 'x' with
    'X') -- in one sorted order, so writers never interleave on a shared key (astra alias r1, r3). Stage 2: every
    dictionary writer (digestion, backfill) already holds the host dictionary lock exclusive from its transaction start,
    so name locks never interleave across writers; a subject merge or its undo takes it exclusive too (astra merge r1)."""
    cur.execute("select pg_advisory_xact_lock_shared(hashtextextended(%s, 0))", (f"lh-dict/{host}",))
    for key in sorted({norm(name), core(name)}):
        cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"lh-alias/{host}/{domain}/{key}",))


def add_declared_alias(cur, host, alias, subject_id, fragment_id, revision):
    """Stage 1 (2026-10-03): register a parent-declared other name of the same thing, keyed by norm() only (no tail
    stripping for declared names). -> ('added'|'same'|'conflict', the existing subject or None). Never merges two
    existing subjects: a name already mapped to another subject is a conflict for approval."""
    cur.execute('select domain from knowledge.subject where subject_id=%s and host_id=%s', (subject_id, host))
    domain = cur.fetchone()[0]
    _lock_name(cur, host, alias, domain)
    hit = lookup(cur, host, alias, domain)
    if hit and hit["subject_id"] == subject_id:
        if hit.get("kind") == "declared":  # re-declared: the evidence moves to this fragment revision (astra alias r1)
            cur.execute("""update knowledge.subject_alias set source_fragment_id=%s, source_revision=%s
                           where host_id=%s and alias=%s and domain=%s and kind='declared'""", (fragment_id, revision, host, norm(alias), domain))
        return "same", hit
    if hit:
        return "conflict", hit
    cur.execute("""insert into knowledge.subject_alias(host_id,alias,subject_id,kind,source_fragment_id,source_revision)
                   values (%s,%s,%s,'declared',%s,%s) on conflict (host_id, domain, alias) do nothing returning 1""",
                (host, norm(alias), subject_id, fragment_id, revision))
    return ("added", None) if cur.fetchone() else ("conflict", lookup(cur, host, alias, domain))


def sentences(cur, subject_id, k=2):
    cur.execute("""select text from knowledge.fragment where subject_id=%s and active
                   order by updated_at desc limit %s""", (subject_id, k))
    return [r[0][:160] for r in cur.fetchall()]


def nearest(cur, host, vector, after=None, k=None, domain=None):
    """Nearest K surviving objects, without a score floor; after restricts registration time."""
    from embedding_guard import assert_host
    k = top_k(k)
    assert_host(cur, host)
    v = _vec(vector)
    # stage 2: a merged subject's vector stays (no rewrite, easy undo); it resolves to the survivor, one candidate per root
    # (astra merge r1 P2-8: the closest vector per root first, then the limit)
    cur.execute("""select root_id, name, sim from (
                     select distinct on (r.subject_id) r.subject_id::text as root_id, r.name,
                            1 - (e.embedding operator(extensions.<=>) %s::extensions.vector) as sim
                     from knowledge.subject s
                     join knowledge.subject_embedding e on e.subject_id=s.subject_id and e.model_id=%s
                     join knowledge.subject r on r.subject_id = coalesce(s.merged_into, s.subject_id)
                     where s.host_id=%s and (%s::text is null or s.domain=%s)
                       and (%s::timestamptz is null or s.created_at > %s::timestamptz)
                     order by r.subject_id, e.embedding operator(extensions.<=>) %s::extensions.vector) x
                   order by sim desc, root_id limit %s""",
                (v, embed.MODEL_ID, host, domain, domain, after, after, v, k))
    out = [{"subject_id": r[0], "name": r[1], "cos": float(r[2])} for r in cur.fetchall()]
    for c in out:
        c["sentences"] = sentences(cur, c["subject_id"])
        cur.execute('select description,domain,subject_type from knowledge.subject where subject_id=%s',(c['subject_id'],))
        c['description'], c['domain'], c['subject_type'] = cur.fetchone()
    return out


CODE_LIKE = re.compile(r"[A-Za-z_`./]")


def _compatible(a, b):
    """flow3 r2 (2026-10-01): never ask Jev to merge a name into one that contains it ('도메인 설명' / '도메인 설명 길이' are
    two things) or a code identifier into a Korean concept ('domain describe' / '도메인 설명')."""
    x, y = re.sub(r"\s+", "", core(a)), re.sub(r"\s+", "", core(b))
    if x != y and (x in y or y in x):
        return False
    return bool(CODE_LIKE.search(a)) == bool(CODE_LIKE.search(b))


def decide(name, cands, same, sentence=None):
    """same(state, texts, question) -> [probability]; Jev sees both subjects with their sentences (JSON)."""
    cands = list(cands or [])[:top_k()]
    if not cands or same is None:
        return None
    state = {"new": {"name": name, "sentences": [sentence[:200]] if isinstance(sentence, str) and sentence else []}}
    items = [json.dumps({"name": c["name"], "domain": c.get('domain'), "description": c.get('description', ''), "subject_type": c.get('subject_type'), "sentences": c.get("sentences") or []}, ensure_ascii=False) for c in cands]
    state['candidates'] = [json.loads(item) for item in items]
    items.append(json.dumps({'none': True, 'name': '해당 없음', 'description': '위 후보 중 같은 대상이 없다'}, ensure_ascii=False))
    scores = same(state, items, SAME_Q + ' items[{i}].none 이 true이면 위 후보 모두가 state.new와 다른 대상인지 판단하라.')
    best, k = max(((float(s), k) for k, s in enumerate(scores[:len(cands)])
                   if _compatible(name, cands[k]['name'])), default=(0.0, None))
    none = float(scores[len(cands)]) if len(scores) > len(cands) else 0.0
    return cands[k] if k is not None and best >= SAME_KEEP and best >= none else None


PARTICLES = (("으로", "로"), ("은", "는"), ("이", "가"), ("을", "를"), ("과", "와"))


def _jong(word):
    w = re.sub(r"[`'\"\s)]+$", "", word)
    ch = w[-1:] if w else ""
    if not ch:
        return False
    o = ord(ch) - 0xAC00
    if 0 <= o < 11172:
        return o % 28 != 0
    if ch.isdigit():
        return ch in "013678"
    return ch.lower() in "lmn" or w.lower().endswith("ng")


def _particle(new, p):
    for a, b in PARTICLES:
        if p in (a, b):
            if a == "으로":
                return "로" if (not _jong(new) or new.rstrip("`").endswith(("ㄹ", "l", "L"))) else "으로"
            return a if _jong(new) else b
    return p


def rewrite(text, old, new):
    """Replace the subject where it stands as a word (whole name, any particle fixed for the new name). An occurrence that
    already reads as the longer new name is left alone: '도메인 설명 길이는' would become '도메인 설명 길이 길이는' (flow3 r2)."""
    if not old or not new or old == new or old not in text:
        return text
    pat = re.compile(r"(?<![0-9A-Za-z가-힣_])" + re.escape(old) +
                     r"(으로|로|은|는|이|가|을|를|과|와|에서|에게|에|의|도|만|부터|까지|보다|처럼)?(?![0-9A-Za-z가-힣_])")
    def one(m):
        if len(new) > len(old) and text.startswith(new, m.start()):
            return m.group(0)
        return new + (_particle(new, m.group(1)) if m.group(1) else "")
    return pat.sub(one, text)


def identity_text(name, description=None):
    return name + (': ' + description if description else '')


def with_known_aliases(cur, host, fact, domain):
    """Only same-area persisted aliases can satisfy an otherwise missing verbatim subject."""
    from subject_contract import subject_in_fact
    if not domain or subject_in_fact(fact): return fact
    hit = lookup(cur, host, fact.get('subject'), domain) if isinstance(fact.get('subject'),str) else None
    if not hit: return fact
    cur.execute('select alias from knowledge.subject_alias where host_id=%s and domain=%s and subject_id=%s',
                (host, domain, hit['subject_id']))
    matches = [a for (a,) in cur.fetchall() if a != fact.get('subject') and a in fact.get('fact','')]
    return {**fact, 'aliases':list(dict.fromkeys([*fact.get('aliases',[]), *matches]))}


def add_alias(cur, host, alias, subject_id):
    cur.execute('select domain from knowledge.subject where host_id=%s and subject_id=%s', (host, subject_id))
    _lock_name(cur, host, alias, cur.fetchone()[0])
    for key in dict.fromkeys((alias, norm(alias), core(alias))):
        cur.execute("""insert into knowledge.subject_alias(host_id,alias,subject_id) values (%s,%s,%s)
                       on conflict (host_id, domain, alias) do nothing""", (host, key, subject_id))


def register(cur, host, name, vector=None, allow_embed=True, domain=None, *, subject_type=None, description=None, parent_subject=None, subject_id=None):
    """Digestion only. Returns subject_id (existing when any spelling already maps to it). allow_embed=False inside the
    digestion transaction: no embedding service call there (astra big review r2); store_pg.embed_unit fills it after."""
    from subject_contract import hierarchy_errors
    errors = hierarchy_errors({'subject':name,'domain':domain,'parent_subject':parent_subject})
    if errors: raise ValueError(str(errors))
    if subject_id is not None:
        import runner
        from identity_ref import materialize, IdentityError
        errors = runner.metadata_errors({'subject':name,'domain':domain,'subject_type':subject_type,'subject_description':description})
        if runner.subject_error(name) or errors or parent_subject:
            raise IdentityError('E_IDENTITY: invalid selected subject metadata')
        return materialize(cur, {'host_id':host,'domain':domain,'subject_id':subject_id},
                           host, domain, name, description, subject_type)
    parent_id = None
    if parent_subject:
        parent_id = register(cur, host, parent_subject['name'], allow_embed=False, domain=domain,
                             subject_type=parent_subject['subject_type'], description=parent_subject['description'])
        for alias in parent_subject.get('aliases', []):
            add_alias(cur, host, alias, parent_id)
    _lock_name(cur, host, name, domain)
    hit = lookup(cur, host, name, domain)
    if hit:
        obj = follow(cur, host, hit['subject_id']) if parent_subject else {}
        if parent_subject and obj.get('parent_subject_id') != parent_id:
            raise ValueError('E_SUBJECT_PARENT: existing subject owns its parent')
        return hit["subject_id"]
    import runner
    errors = runner.metadata_errors({'subject':name,'domain':domain,'subject_type':subject_type,'subject_description':description})
    if runner.subject_error(name) or errors:
        raise ValueError('new feature subject requires noun name, fixed type and one-line description: ' + str(errors))
    domain = domain or ('subject:' + name.strip())
    cur.execute('insert into knowledge.domain_type(host_id,domain,maintenance_pending) values(%s,%s,true) on conflict do nothing', (host, domain))
    cur.execute("""insert into knowledge.subject(host_id,domain,name,subject_type,description,parent_subject_id) values (%s,%s,%s,%s,%s,%s)
                   on conflict (host_id,domain,name) do update set name=excluded.name returning subject_id::text""",
                (host, domain, name, subject_type, description.strip(), parent_id))
    sid = cur.fetchone()[0]
    add_alias(cur, host, name, sid)
    if vector is None and allow_embed:
        try:
            vector = embed.encode_queries([identity_text(name, description)])[0]
        except embed.EmbeddingUnavailable:
            vector = None
    if vector is not None:
        from embedding_guard import assert_host
        assert_host(cur,host)
        cur.execute("""insert into knowledge.subject_embedding(subject_id,model_id,embedding) values (%s,%s,%s::extensions.vector)
                       on conflict (subject_id,model_id) do update set embedding=excluded.embedding""",
                    (sid, embed.MODEL_ID, _vec(vector)))
    return sid


def follow(cur, host, subject_id):
    """Read-only cross-domain leaf resolution, following merged_into without copying."""
    seen = set()
    while subject_id and subject_id not in seen:
        seen.add(subject_id)
        cur.execute('select subject_id::text,name,domain,merged_into::text,subject_type,description,parent_subject_id::text from knowledge.subject where host_id=%s and subject_id=%s',
                    (host, subject_id))
        row = cur.fetchone()
        if not row:
            raise ValueError('leaf subject not found for host')
        if not row[3]:
            obj = {'subject_id': row[0], 'name': row[1], 'domain': row[2], 'subject_type':row[4], 'description':row[5],
                   'parent_subject_id':row[6], 'parent_subject':None}
            if row[6]:
                cur.execute('select name,subject_type,description from knowledge.subject where host_id=%s and subject_id=%s', (host,row[6]))
                parent = cur.fetchone()
                obj['parent_subject'] = dict(name=parent[0],subject_type=parent[1],description=parent[2])
            return obj
        subject_id = row[3]
    raise ValueError('subject merge cycle')


def maintain(cur, host, domain, describe, embed_fn, merge_luna=None, merge_same=None):
    """Owned partition metadata and duplicate cleanup; all model functions are injected."""
    import domain_router
    import subject_merge
    from model_output import ModelOutputInvalid
    from embedding_guard import assert_host
    assert_host(cur,host)
    domain_router.lock_partition(cur, host, domain)
    cur.execute('''select subject_id::text,name,description,subject_type from knowledge.subject
        where host_id=%s and domain=%s and merged_into is null order by subject_id''', (host, domain))
    subjects = cur.fetchall()
    described = []
    failures = []
    vector_updates = []
    domain_description = None
    for sid, name, old, subject_type in subjects:
        cur.execute('select id::text,text,source from knowledge.fragment where subject_id=%s and active order by id', (sid,))
        evidence = cur.fetchall()
        try:
            description = describe({'domain': domain, 'name': name, 'subject_type':subject_type, 'description': old, 'fragments': evidence})
        except ModelOutputInvalid as exc:
            # Keep text, evidence, timestamp and vector unchanged (including an empty old text).
            described.append({'subject_id':sid,'name':name,'subject_type':subject_type,'description':old,'evidence':evidence})
            failures.append({'status':'description_failed','subject_id':sid,'error':str(exc)})
            continue
        if not isinstance(description, str) or not description.strip():
            raise ValueError('description writer must return nonempty text')
        vector_updates.append((sid, identity_text(name, description)))
        described.append({'subject_id':sid,'name':name,'subject_type':subject_type,'description':description,'evidence':evidence})
        cur.execute('''update knowledge.subject set description=%s,description_evidence=%s,description_updated_at=now()
            where subject_id=%s''', (description, json.dumps(evidence, default=str), sid))
    try:
        description = describe({'domain': domain, 'subjects': described})
    except ModelOutputInvalid as exc:
        failures.append({'status':'description_failed','domain':domain,'error':str(exc)})
    else:
        if not isinstance(description, str) or not description.strip():
            raise ValueError('domain description writer must return nonempty text')
        description = description[:300]
        domain_description = identity_text(domain, description)
        domain_router.describe(cur, host, domain, description)
        cur.execute('''update knowledge.domain_type set description_evidence=%s,description_updated_at=now(),
            maintenance_pending=false where host_id=%s and domain=%s''',(json.dumps(described,default=str),host,domain))
    # Descriptions do not affect another description writer; publish all vectors before merge decisions.
    texts = list(dict.fromkeys([d for _, d in vector_updates] +
                              ([domain_description] if domain_description is not None else []) +
                              ([identity_text(s['name'],s['description']) for s in described if s['description']] if merge_luna and merge_same else [])))
    vectors = dict(zip(texts, embed_fn(texts))) if texts else {}
    for sid, description in vector_updates:
        cur.execute('''insert into knowledge.subject_embedding(subject_id,model_id,embedding) values(%s,%s,%s::extensions.vector)
            on conflict(subject_id,model_id) do update set embedding=excluded.embedding''', (sid, embed.MODEL_ID, _vec(vectors[description])))
    if domain_description is not None:
        vector = vectors[domain_description]
        cur.execute('''insert into knowledge.domain_embedding(host_id,domain,model_id,embedding) values(%s,%s,%s,%s::extensions.vector)
            on conflict(host_id,domain,model_id) do update set embedding=excluded.embedding''', (host, domain, embed.MODEL_ID, _vec(vector)))
    outcomes = failures
    if merge_luna and merge_same:
        for sid, name, _, _ in subjects:
            cur.execute('select merged_into,description from knowledge.subject where subject_id=%s', (sid,))
            merged, description = cur.fetchone()
            if merged:
                continue
            # A merge may have changed the description; this decision needs that new vector immediately.
            description = identity_text(name, description)
            vector = vectors.get(description)
            if vector is None:
                vector = embed_fn([description])[0]
                vectors[description] = vector
            for candidate in nearest(cur, host, vector, domain=domain):
                other = candidate['subject_id']
                if sid < other:
                    outcomes.append(subject_merge.automatic_merge(cur, host, other, sid, merge_luna, merge_same))
    return outcomes


def resolver(connect, dsn, host, unit=None, same=None, embed_fn=None, domain=None):
    """Record time (read only). resolve(name, sentence) -> (name_to_store, how);
    how in exact|alias|same_entity|pending|new|new_unchecked. pending = names still new in this work unit."""
    efn = embed_fn or embed.encode_queries
    pending = {}  # core -> (name, sentence)
    if unit:
        with connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("""select judgement_body from knowledge.ledger_entry where work_unit_id=%s
                           and state::text not in ('closed','expired','rejected_input') order by created_at""", (unit,))
            for (body,) in cur.fetchall():
                for f in body.get("facts", []):
                    if f.get("subject_new") and isinstance(f.get("subject"), str):
                        pending.setdefault(core(f["subject"]), (f["subject"], f.get("fact", "")))

    def resolve(name, sentence=None):
        resolve.provisional = {"status": "PROVISIONAL", "candidate": "new subject candidate", "as_written": name}
        with connect(dsn) as conn, conn.cursor() as cur:
            hit = lookup(cur, host, name, domain) if domain else lookup(cur, host, name)
            if hit:
                resolve.provisional.update(follow(cur, host, hit['subject_id']), candidate='matched existing object')
        if hit and hit.get("kind") in ("declared", "merged"):
            return name, "declared_alias"  # the written name stays in the text; the subject id is resolved at digestion
        if hit:
            return hit["name"], ("exact" if hit["name"] == name else "alias")
        c = core(name)
        if c in pending:
            return pending[c][0], "pending"
        if same is None:
            pending[c] = (name, sentence or "")
            return name, "new_unchecked"
        try:
            v = efn([c])[0]
        except embed.EmbeddingUnavailable:
            pending[c] = (name, sentence or "")
            return name, "new_unchecked"
        with connect(dsn) as conn, conn.cursor() as cur:
            cands = nearest(cur, host, v, domain=domain) if domain else nearest(cur, host, v)
        if pending:  # Jev is called with no transaction open
            keys = list(pending)
            cands += [{"subject_id": None, "name": pending[k][0], "sentences": [pending[k][1][:160]], "cos": cc}
                      for k, pv in zip(keys, efn(keys)) for cc in [cos(v, pv)]]
        cands = sorted(cands, key=lambda row: (-row['cos'], row['name']))[:top_k()]
        best = decide(name, cands, same, sentence)
        if best:
            if best['subject_id']:
                with connect(dsn) as conn, conn.cursor() as cur:
                    resolve.provisional.update(follow(cur, host, best['subject_id']), candidate='matched existing object')
            return best["name"], ("same_entity" if best["subject_id"] else "pending")
        pending[c] = (name, sentence or "")
        return name, "new"
    return resolve
