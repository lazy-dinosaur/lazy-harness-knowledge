"""Strict host vector-space checks; administration (calibrate/reembed) uses raw reads."""
from embedding_models import ModelMismatch

VECTOR_COLUMNS = {('knowledge','fragment_embedding','embedding'),
                  ('knowledge','subject_embedding','embedding'),
                  ('knowledge','domain_embedding','embedding')}


def assert_host(cur, host, model_id=None):
    import embed
    active = model_id or embed.MODEL_ID
    cur.execute('''select distinct model_id from (
        select e.model_id from knowledge.fragment_embedding e join knowledge.fragment f on f.id=e.fragment_id where f.host_id=%s
        union all select e.model_id from knowledge.subject_embedding e join knowledge.subject s using(subject_id) where s.host_id=%s
        union all select model_id from knowledge.domain_embedding where host_id=%s) models''',(host,host,host))
    stored = {r[0] for r in cur.fetchall()}
    if stored - {active}:
        raise ModelMismatch('stored vector model mismatch; explicit checkpoint reembed required: '+str(sorted(stored)))
    cur.execute("select to_regclass('g16_experiment.embedding_state')")
    if cur.fetchone()[0] is not None:
        cur.execute('select model_id,status from g16_experiment.embedding_state where host_id=%s',(host,))
        row = cur.fetchone()
        if row and (row[0] != active or row[1] != 'ready'):
            raise ModelMismatch('checkpoint reembed is not ready for active model')


def assert_dsn(dsn, host):
    import store_pg
    with store_pg.connect(dsn) as conn, conn.cursor() as cur:
        assert_host(cur,host)


def assert_unit(dsn, unit_id):
    import store_pg
    with store_pg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute('select host_id from knowledge.work_unit where work_unit_id=%s',(unit_id,))
        row=cur.fetchone()
        if row:
            assert_host(cur,row[0])

def inspect_vector_columns(cur):
    cur.execute('''select n.nspname,c.relname,a.attname from pg_attribute a
        join pg_class c on c.oid=a.attrelid join pg_namespace n on n.oid=c.relnamespace
        join pg_type t on t.oid=a.atttypid
        where n.nspname in ('knowledge','rules','g16_experiment') and c.relkind in ('r','p')
        and a.attnum>0 and not a.attisdropped and t.typname in ('vector','halfvec','sparsevec')''')
    columns = {tuple(r) for r in cur.fetchall()}
    if columns != VECTOR_COLUMNS:
        raise ModelMismatch('unmapped/missing vector columns; refuse incomplete reembed: '+str(sorted(columns ^ VECTOR_COLUMNS)))
    return sorted(columns)
