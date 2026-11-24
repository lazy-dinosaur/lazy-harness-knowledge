"""Stage 6 production store port (user option A): a fresh owned local Postgres and the existing DB parts.

`fresh_db(plan, out)` starts a throw-away local container (no pull, unique name per run), applies the module migrations,
registers the plan host, relaxes `subject_type` like the unit-first pilot schema, yields the DSN and keeps a dump in
`<out>/store-db.dump` for inspection. `StageStore` implements the `stagewise.run_store` port over the existing parts:
`place_write_absorb.install_rows`, `record_unit` + `knowledge_cli.record`, the worktime check, `digest_driver.run_digestion`
with the placed domain (no domain re-routing) and `store_pg.search` mapped back to work unit / domain / subject.
"""
import contextlib, hashlib, os, re
from pathlib import Path

from safety import Stop, save


@contextlib.contextmanager
def fresh_db(plan, out):
    import local_db as ldb, store_pg as pg, time
    name = 'lh-knowledge-stage6-' + re.sub(r'[^a-z0-9-]', '-', plan['run'].lower())
    host = plan.get('host', 'knowledge-local')
    if ldb.docker('ps', '-a', '--filter', f'name=^/{name}$', '--format', '{{.Names}}').strip():
        raise Stop('stage-6 container name already exists; inspect manually')
    ldb.docker('image', 'inspect', ldb.IMAGE)  # no pull
    started = False
    try:
        ldb.docker('run', '-d', '--rm', '--name', name, '-p', '127.0.0.1::5432', '-e', 'POSTGRES_PASSWORD=localtest', ldb.IMAGE)
        started = True
        port = ldb.docker('port', name, '5432/tcp').decode().strip().rsplit(':', 1)[-1]
        if not port.isdigit(): raise Stop('invalid local port')
        dsn = f'postgresql://postgres:localtest@127.0.0.1:{port}/postgres'
        for _ in range(120):
            try:
                with pg.connect(dsn) as conn, conn.cursor() as cur: cur.execute('select 1')
                break
            except pg.driver.Error: time.sleep(1)
        else: raise Stop('local DB readiness timeout')
        with pg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute('create schema if not exists extensions; create extension if not exists vector with schema extensions; create extension if not exists pg_trgm with schema extensions')
        for migration in ldb.MIGRATIONS:
            ldb.docker('cp', str(ldb.RUNNER/'migrations'/(migration+'.sql')), name+':/tmp/migration.sql')
            ldb.docker('exec', name, 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'postgres', '-f', '/tmp/migration.sql')
        ldb.preflight_validation_receipts(dsn)
        with pg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute('insert into knowledge.host(host_id,name,repo_locator) values(%s,%s,%s)', (host, host, str(out)))
            # same relaxation as the unit-first pilot schema: stagewise subjects carry no subject_type
            cur.execute('alter table knowledge.subject alter column subject_type drop not null')
            cur.execute('alter table knowledge.subject drop constraint if exists subject_type_fixed')
        yield dsn
        raw = ldb.docker('exec', name, 'pg_dump', '-U', 'postgres', '-d', 'postgres', '-Fc', '-n', 'knowledge')
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out)/'store-db.dump').write_bytes(raw)
        save(Path(out)/'store-db.json', {'sha256': hashlib.sha256(raw).hexdigest(), 'migrations': ldb.MIGRATIONS, 'container': name})
    finally:
        if started:
            ldb.docker('rm', '-f', '-v', name)


class StageStore:
    def __init__(self, dsn, host, plan, broker):
        import config, generation as p
        from safety import Ledger, Paid
        from active_judgements import make_actor_judge
        from safety import STATE_HOME
        cfg = config.require('jev_api_key', 'jev_base_url', 'jev_model')
        ledger = Ledger(STATE_HOME/'runs'/plan['run']/'paid.jsonl', plan['run'], {'generation': 10 ** 6, 'maintenance': 10 ** 6},
                        cash_limit=float(plan['cash_cap_usd']))
        self.paid = Paid(ledger, 'generation', cfg)
        self.dsn, self.host = dsn, host
        self.adapt, _ = p.adapters(cfg, self.paid, worker_factory=broker.worker_factory)
        self.judge = make_actor_judge(cfg, paid=self.paid)

    def install(self, rows):
        import embed
        from place_write_absorb import install_rows
        active = [{'unit_id': r['unit_id'], 'fact': r['fact'], 'domain': {'chosen': r['domain']}, 'subject': {'chosen': r['subject']}}
                  for r in rows]
        held, _ = install_rows(self.dsn, self.host, active, embed.encode_queries)
        return {uid: (v.get('errors') or [{}])[0].get('detail') for uid, v in held.items()}

    def record(self, unit_id, fact):
        import knowledge_cli as cli, store_pg as pg, worktime_driver as wd
        from place_write_absorb import record_unit
        keys = ('fact', 'subject', 'domain', 'aliases', 'evidence_refs', 'identity_ref', 'subject_id', 'source_unit')
        identity = {k: fact.get(k) for k in keys}
        def check(index, candidate):
            return [] if index == 0 and all(candidate.get(k) == v for k, v in identity.items()) else [
                {'code': 'E_GROUNDING_DRIFT', 'where': 'fact', 'detail': 'stagewise sentence/identity drift'}]
        self.paid.scope = unit_id
        result = record_unit(unit_id, fact, lambda data, **kw: cli.record(self.dsn, data, same=None, embed_fn=self.adapt['embed_fn'], **kw),
                             self.host, check)
        if result.get('errors') or not result.get('entry_id'):
            return None
        wd.run_worktime(self.dsn, 1, self.judge, entry_ids=[result['entry_id']], semantic_grounding=True)
        with pg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute('select status::text from knowledge.work_unit where work_unit_id=%s', (unit_id,))
            status = cur.fetchone()[0]
        if status == 'active':
            pg.complete(self.dsn, unit_id)
        return result['entry_id']

    def digest(self, unit_id, domain):
        import digest_driver as dd
        out = dd.run_digestion(self.dsn, unit_id, self.judge, domain=domain, maintenance=None, workers=1,
                               same=self.adapt['same'], embed_fn=self.adapt['embed_fn'])
        return {k: out.get(k) for k in ('decisions', 'absorbed', 'held', 'status') if isinstance(out, dict) and k in out} or {'done': True}

    def find(self, text, k):
        import store_pg as pg
        pg.backfill_embeddings(self.dsn, self.host, variant='ctx-v2')
        hits = pg.search(self.dsn, self.host, text, limit=k, mode='hybrid')
        out = []
        with pg.connect(self.dsn) as conn, conn.cursor() as cur:
            for h in hits:
                cur.execute('''select f.domain, s.name, a.work_unit_id::text from knowledge.fragment f
                    left join knowledge.subject s on s.subject_id = f.subject_id
                    left join knowledge.absorption a on a.fragment_ref = f.id where f.id = %s limit 1''', (h['id'],))
                row = cur.fetchone()
                if row:
                    out.append({'unit_id': row[2], 'domain': row[0], 'subject': row[1], 'fact': h.get('text')})
        return out
