"""Owned disposable container; only root-local dump persists between Main commands."""
import contextlib
import hashlib
import os
import subprocess
import time
from safety import HERE, RUNNER, Stop, save, load

IMAGE='public.ecr.aws/supabase/postgres:17.6.1.134'
NAME='lh-knowledge-local-local'
from scopes import LOCAL_HOST as HOST
MIGRATIONS=['0001_knowledge_init','0002_search_and_cost','0003_embedding_hybrid','0004_work_unit_baseline',
 '0005_domain_type','0006_rules','0007_harness_rules_out','0008_embedding_any_dim','0009_subject_dictionary',
 '0010_fact_form','0011_structure_fill_once','0012_contradiction_resolution','0013_declared_alias',
 '0014_subject_merge','0015_source_review_receipt','0016_source_correction','0017_source_hint_resolution',
 '0018_source_metadata_repair','0019_domain_subject','0020_partition_actor','0021_subject_type','0022_subject_parent',
 '0023_validation_receipt']

# Exact columns consumed by validation_receipts.insert/read, including the event FK.
RECEIPT_COLUMNS = {
    'receipt_id': ('uuid', 'NO'), 'request_id': ('uuid', 'NO'),
    'contract_version': ('text', 'NO'), 'profile': ('text', 'NO'), 'stage': ('text', 'NO'),
    'entry_id': ('uuid', 'YES'), 'host_id': ('text', 'YES'), 'work_unit_id': ('text', 'YES'),
    'judgement_id': ('text', 'YES'), 'judgement_version': ('int4', 'YES'),
    'fact_index': ('int4', 'YES'), 'payload_digest': ('text', 'NO'),
    'checked_digest': ('text', 'NO'), 'payload': ('jsonb', 'NO'),
    'checked_payload': ('jsonb', 'NO'), 'errors': ('jsonb', 'NO'),
    'created_at': ('timestamptz', 'NO'),
}

def preflight_validation_receipts(dsn):
    """Read-only 0023 admission; never repair/replay DDL against a restored dump.

    A normal old-schema registration may succeed, but failure preservation requires
    this table, insert/read columns, primary key, and validated event reference.
    """
    import store_pg as pg
    message = '0023 validation receipt schema required; STOP, no automatic restore migration'
    with pg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""select table_name,column_name,udt_name,is_nullable
            from information_schema.columns where table_schema='knowledge'
            and table_name in ('validation_receipt','ledger_entry_event')""")
        columns = {(t, c): (typ, null) for t, c, typ, null in cur.fetchall()}
        if any(columns.get(('validation_receipt', c)) != shape for c, shape in RECEIPT_COLUMNS.items()):
            raise Stop(message + ' (missing/partial receipt columns)')
        if columns.get(('ledger_entry_event', 'validation_receipt_ref')) != ('uuid', 'YES'):
            raise Stop(message + ' (missing/partial event column)')
        cur.execute("""select
            exists (select 1 from pg_constraint c
                join pg_attribute a on a.attrelid=c.conrelid and a.attnum=c.conkey[1]
                where c.conrelid='knowledge.validation_receipt'::regclass
                and c.contype='p' and c.convalidated and cardinality(c.conkey)=1
                and a.attname='receipt_id'),
            exists (select 1 from pg_constraint c
                join pg_attribute a on a.attrelid=c.conrelid and a.attnum=c.conkey[1]
                join pg_attribute r on r.attrelid=c.confrelid and r.attnum=c.confkey[1]
                where c.conrelid='knowledge.ledger_entry_event'::regclass
                and c.confrelid='knowledge.validation_receipt'::regclass
                and c.contype='f' and c.convalidated
                and cardinality(c.conkey)=1 and cardinality(c.confkey)=1
                and a.attname='validation_receipt_ref' and r.attname='receipt_id')
            """)
        if cur.fetchone() != (True, True):
            raise Stop(message + ' (missing/unvalidated receipt PK or event FK)')

def _owned_unit_first_plan(home, host, name, binding, dsn=None):
    from scopes import AREA_SCOPES
    from safety import digest
    plan_path = home/'state/plan.json'
    if not plan_path.exists(): raise Stop('exact owned unit-first state plan required')
    plan = load(plan_path)
    scope = plan.get('scope')
    if (scope not in AREA_SCOPES or home.resolve() != (HERE/scope).resolve()
        or (HERE/scope).resolve() != HERE.resolve()/scope
        or plan.get('write_path') != 2
        or plan.get('host') != host or host != 'knowledge-local-'+scope
        or plan.get('container') != name or name != 'lh-knowledge-local-'+scope+'-local'
        or digest(plan) != binding):
        raise Stop('untyped schema change requires exact owned local unit-first pilot')
    if dsn is not None:
        import re
        match = re.fullmatch(r'postgresql://postgres:localtest@127\.0\.0\.1:([0-9]+)/postgres', dsn)
        if not match or not 1 <= int(match[1]) <= 65535:
            raise Stop('untyped schema change requires exact owned local unit-first pilot DSN')
    return plan

def apply_unit_first_schema(home, host, name, binding, dsn):
    """ONLY called after the owned localhost container's host allowlist check."""
    plan_path = home/'state/plan.json'
    if not plan_path.exists(): return
    plan = load(plan_path)
    if plan.get('write_path') != 2: return
    plan = _owned_unit_first_plan(home, host, name, binding, dsn)
    scope = plan['scope']
    import store_pg as pg
    with pg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute('alter table knowledge.subject alter column subject_type drop not null')
        cur.execute('alter table knowledge.subject drop constraint if exists subject_type_fixed')
    save(home/'state/unit-first-schema.json',{'binding':binding,'scope':scope,
        'changes':['subject_type DROP NOT NULL','DROP CONSTRAINT subject_type_fixed'],
        'description_required':True,'placeholder_type':False,'remote_allowed':False})


def docker(*args):
    return subprocess.run(['docker',*args],capture_output=True,check=True,timeout=180).stdout

@contextlib.contextmanager
def database(binding, *, home=None, name=None, host=None):
    import store_pg as pg
    home=home or HERE; name=name or NAME; host=host or HOST
    from scopes import PRIOR_REQUIRED_SCOPES
    # Fixed fresh namespaces must pass exact ownership before any Docker call.
    state_plan = home/'state/plan.json'
    if (any(home.name == scope or scope in name or scope in host for scope in PRIOR_REQUIRED_SCOPES)
        or (state_plan.exists() and load(state_plan).get('scope') in PRIOR_REQUIRED_SCOPES)):
        _owned_unit_first_plan(home, host, name, binding)
    if docker('ps','-a','--filter',f'name=^/{name}$','--format','{{.Names}}').strip():
        raise Stop('owned name already exists; inspect manually, no container touched')
    docker('image','inspect',IMAGE)  # no pull
    started=ready=False
    dump=home/'state/database.dump'; meta=home/'state/database.json'
    try:
        docker('run','-d','--rm','--name',name,'-p','127.0.0.1::5432','-e','POSTGRES_PASSWORD=localtest',IMAGE)
        started=True
        port=docker('port',name,'5432/tcp').decode().strip().rsplit(':',1)[-1]
        if not port.isdigit(): raise Stop('invalid local port')
        dsn=f'postgresql://postgres:localtest@127.0.0.1:{port}/postgres'
        for _ in range(120):
            try:
                with pg.connect(dsn) as conn, conn.cursor() as cur: cur.execute('select 1')
                break
            except pg.driver.Error: time.sleep(1)
        else: raise Stop('local DB readiness timeout')
        with pg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute('create schema if not exists extensions; create extension if not exists vector with schema extensions; create extension if not exists pg_trgm with schema extensions')
        if dump.exists():
            m=load(meta)
            if m['binding']!=binding or m['sha256']!=hashlib.sha256(dump.read_bytes()).hexdigest():
                raise Stop('local dump binding mismatch')
            docker('cp',str(dump),name+':/tmp/state.dump')
            docker('exec',name,'pg_restore','-U','postgres','-d','postgres','--no-owner','--no-privileges','--exit-on-error','/tmp/state.dump')
        else:
            if meta.exists(): raise Stop('dump missing; do not silently rebuild')
            for migration in MIGRATIONS:
                docker('cp',str(RUNNER/'migrations'/(migration+'.sql')),name+':/tmp/migration.sql')
                docker('exec',name,'psql','-X','-v','ON_ERROR_STOP=1','-U','postgres','-d','postgres','-f','/tmp/migration.sql')
        # Both fresh and restored schemas must support diagnostic failure writes.
        # Old/partial restores STOP before schema relaxation, progress rebind, yield, or dump save.
        preflight_validation_receipts(dsn)
        if not dump.exists():
            with pg.connect(dsn) as conn, conn.cursor() as cur:
                cur.execute('insert into knowledge.host(host_id,name,repo_locator) values(%s,%s,%s)',(host,host,str(HERE/'v1')))
                cur.execute('create schema g16_experiment; create table g16_experiment.progress (step text primary key, binding text not null)')
        # Refuse any foreign host even in a local restored dump.
        with pg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute('select host_id from knowledge.host')
            if cur.fetchall()!=[(host,)]: raise Stop('foreign host in local DB')
        apply_unit_first_schema(home, host, name, binding, dsn)
        # Imported binary dump is immutable; rebind only experiment checkpoints after restore.
        if dump.exists() and m.get('progress_bindings'):
            with pg.connect(dsn) as conn, conn.cursor() as cur:
                cur.execute('select distinct binding from g16_experiment.progress')
                allowed=set(m['progress_bindings']) | {binding}
                if any(row[0] not in allowed for row in cur.fetchall()):
                    raise Stop('foreign progress binding in local dump')
                cur.execute('update g16_experiment.progress set binding=%s', (binding,))
        ready=True
        os.environ['LH_KNOWLEDGE_DB_URL']=dsn
        yield dsn
    finally:
        try:
            if ready:
                raw=docker('exec',name,'pg_dump','-U','postgres','-d','postgres','-Fc','-n','knowledge','-n','rules','-n','g16_experiment')
                dump.parent.mkdir(exist_ok=True)
                tmp=dump.with_suffix('.tmp'); tmp.write_bytes(raw); tmp.chmod(0o600); os.replace(tmp,dump)
                save(meta,{'binding':binding,'sha256':hashlib.sha256(raw).hexdigest(),'migrations':MIGRATIONS})
        finally:
            if started:
                docker('rm','-f','-v',name)
                if docker('ps','-a','--filter',f'name=^/{name}$','--format','{{.Names}}').strip():
                    raise Stop('container cleanup failed')
