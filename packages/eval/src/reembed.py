"""Owned-checkpoint vector rebuild. Paid/cache batches resume; publication is one DB transaction.
No canonical fact/subject/leaf/history/absorption is modified. Stale vector revisions are rebuilt
from the current canonical revision (vector indexes are derived, not immutable history).
"""
from collections import Counter
from urllib.parse import urlsplit
from safety import Stop, digest, load, save
from local_db import HOST


def local_only(dsn):
    url = urlsplit(dsn)
    if url.scheme not in ('postgres','postgresql') or url.hostname not in ('127.0.0.1','::1') or not url.port:
        raise Stop('model transition requires the owned loopback checkpoint DB')


def _bind_host(plan):
    # The checkpoint belongs to the plan's host (e.g. pilot-feature), not local_db's default name.
    global HOST
    HOST = plan.get('host') or HOST


def inventory(cur, host=None):
    host = host or HOST
    import store_pg as pg
    from subject_dict import core
    from embedding_guard import inspect_vector_columns
    columns = inspect_vector_columns(cur)
    cur.execute('select host_id from knowledge.host order by host_id')
    if cur.fetchall() != [(host,)]:
        raise Stop('foreign host in checkpoint')
    cur.execute('''select f.id::text,f.revision,f.text,f.kind::text,f.domain,f.group_id::text,f.active
        from knowledge.fragment f where f.host_id=%s order by f.id''',(host,))
    fragments = cur.fetchall()
    cur.execute('''select e.fragment_id::text,e.embed_variant from knowledge.fragment_embedding e
        join knowledge.fragment f on f.id=e.fragment_id where f.host_id=%s order by e.fragment_id,e.embed_variant''',(host,))
    variants = {}
    for fid,variant in cur.fetchall():
        pg._variant(variant)  # unknown representations must not silently disappear
        variants.setdefault(fid,set()).add(variant)
    result = []
    for fid,revision,text,kind,domain,group,active in fragments:
        siblings = [r[2][:80] for r in fragments if group is not None and r[5]==group and r[0]!=fid and r[6]][:3]
        for variant in sorted(variants.get(fid,set()) | {'plain'}):
            result.append({'kind':'fragment','id':fid,'revision':revision,'variant':variant,
                'role':'passage','text':pg._embedding_input(kind,domain,siblings,text,variant)})
    # Includes inactive/merged subjects and every parent; leaves reference this dictionary and have no vector column.
    cur.execute('''select subject_id::text,name,description,description_updated_at,parent_subject_id::text
        from knowledge.subject where host_id=%s order by subject_id''',(host,))
    for sid,name,description,described,parent in cur.fetchall():
        text = description if described and description else core(name)
        result.append({'kind':'subject','id':sid,'parent':parent,'role':'query','text':text})
    cur.execute('select domain,description from knowledge.domain_type where host_id=%s order by domain',(host,))
    for domain,description in cur.fetchall():
        result.append({'kind':'domain','id':domain,'role':'query','text':description or domain})
    cur.execute('''select count(*) from knowledge.fragment_leaf l join knowledge.fragment f on f.id=l.fragment_id
        where f.host_id=%s''',(host,))
    leaves = cur.fetchone()[0]
    if any(not isinstance(r['text'],str) or not r['text'] for r in result):
        raise Stop('empty checkpoint embedding representation')
    return result, {'vector_columns':columns,'leaf_rows':leaves,'leaf_vectors':'none; subject_id references subject vectors'}


def _mark(cur, binding, model, status, sha):
    cur.execute('''create table if not exists g16_experiment.embedding_state (
        host_id text primary key references knowledge.host(host_id), model_id text not null,
        status text not null check(status in ('running','ready')), binding text not null,
        inventory_sha text not null, updated_at timestamptz not null default now())''')
    cur.execute('''insert into g16_experiment.embedding_state(host_id,model_id,status,binding,inventory_sha)
        values(%s,%s,%s,%s,%s) on conflict(host_id) do update set model_id=excluded.model_id,
        status=excluded.status,binding=excluded.binding,inventory_sha=excluded.inventory_sha,updated_at=now()''',
        (HOST,model,status,binding,sha))


def publish(cur, rows, model):
    import store_pg as pg
    import embed
    # Replacement only after all model-keyed vectors exist. Any SQL failure rolls everything back.
    for row in rows:
        vector = embed.CACHE.get(model,row['role'],row['text'])
        if vector is None or len(vector) != 1024:
            raise Stop('missing/wrong-dimension reembed vector')
    for table,key,owner in (('fragment_embedding','fragment_id','fragment'),('subject_embedding','subject_id','subject')):
        cur.execute(f'delete from knowledge.{table} e using knowledge.{owner} o where e.{key}=o.'+
                    ('id' if owner=='fragment' else key)+' and o.host_id=%s',(HOST,))
    cur.execute('delete from knowledge.domain_embedding where host_id=%s',(HOST,))
    for row in rows:
        vector = pg._vector(embed.CACHE.get(model,row['role'],row['text']))
        if row['kind']=='fragment':
            cur.execute('''insert into knowledge.fragment_embedding(fragment_id,revision,model_id,embed_variant,embedding)
                values(%s,%s,%s,%s,%s::extensions.vector)''',(row['id'],row['revision'],model,row['variant'],vector))
        elif row['kind']=='subject':
            cur.execute('insert into knowledge.subject_embedding(subject_id,model_id,embedding) values(%s,%s,%s::extensions.vector)',
                (row['id'],model,vector))
        elif row['kind']=='domain':
            cur.execute('insert into knowledge.domain_embedding(host_id,domain,model_id,embedding) values(%s,%s,%s,%s::extensions.vector)',
                (HOST,row['id'],model,vector))
        else:
            raise Stop('unknown reembed vector kind')


def run(plan, home, paid, dsn):
    import embed
    import store_pg as pg
    import precompute
    local_only(dsn)
    _bind_host(plan)
    if plan.get('embedding_precompute'):
        precompute.assert_completed(plan,home)
    binding = digest(plan)
    with pg.connect(dsn) as conn, conn.cursor() as cur:
        rows,audit = inventory(cur)
        sha = digest(rows)
        _mark(cur,binding,embed.MODEL_ID,'running',sha)
    # Metadata stays running across interruption. Search/absorb refuse until atomic publication completes.
    values = {role:list(dict.fromkeys(r['text'] for r in rows if r['role']==role)) for role in ('query','passage')}
    paid.scope = 'reembed-v1:'+sha
    reused = precompute.seed_paid(paid,values,[paid.scope])
    before = embed.CACHE.stats()
    embed.encode_queries(values['query']); embed.encode_passages(values['passage'])
    with pg.connect(dsn) as conn, conn.cursor() as cur:
        current,_ = inventory(cur)
        if digest(current) != sha:
            raise Stop('checkpoint changed during reembed')
        publish(cur,rows,embed.MODEL_ID)
        _mark(cur,binding,embed.MODEL_ID,'ready',sha)
        from embedding_guard import assert_host
        assert_host(cur,HOST)
    result = {'completed':True,'binding':binding,'model_id':embed.MODEL_ID,'inventory_sha':sha,
        'counts':dict(Counter(r['kind'] for r in rows)),
        'paid_vectors_reused':reused,'cache':{k:embed.CACHE.stats()[k]-before[k] for k in before},**audit}
    save(home/'state/reembed.json',result)
    return result


def assert_ready(plan, home, dsn):
    import embed
    from embedding_guard import assert_dsn
    _bind_host(plan)
    path = home/'state/reembed.json'
    if not path.exists():
        raise Stop('reembed must complete before absorb/search/maintenance')
    row = load(path)
    if row.get('completed') is not True or row.get('binding') != digest(plan) or row.get('model_id') != embed.MODEL_ID:
        raise Stop('reembed receipt/model drift')
    assert_dsn(dsn,HOST)
