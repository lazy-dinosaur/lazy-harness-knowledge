"""Stage runner for the knowledge module (issue #22): a frozen plan drives stage 1 (extract) or stage 2 (place) with every
provider attempt counted and capped, cash bounded by the plan, and a dry run that refuses the first provider attempt.

The runner owns no provider code: it installs counting transports through ``host_defaults`` (judgement, embedding) and
wraps the writer/worker factories (P4 ports). Real transports come from the hosts (``hosts/jev``, ``hosts/embed``,
``hosts/pi``) or are injected (tests, or the composition layer).

Plan (JSON, kept outside the repository, e.g. in the state home)::

    {"run": "r1", "stage": "extract" | "place", "host": "knowledge-local",
     "records": [{"record_id": "S1", "path": "docs/a.md", "sha256": "..."}],   # documents under <inputs>/v1
     "extracts": {"S1": "<path to approved stage-1 report>"},               # place only
     "base": "<path to snapshot>",                                         # place only
     "caps": {"sdk": 10, "jev_http": 100, "embedding_post": 10, "embedding_inputs": 100},
     "cash_cap_usd": 0.05, "absolute_ceiling_usd": 1.0, "prior_spent_usd": 0.93, "jev_reserve_usd": 0.0347,
     "approval": "<user approval reference>", "resume": false}

A run name keeps one cash cap across restarts: accounting is persisted after every counted attempt
(``accounting-live.json``); a paid run whose folder already has accounting is refused unless the plan says
``"resume": true``, which continues the counters and cash (an in-flight Jev reserve counts as spent), skips records whose
place report exists and reuses the first-pass rows already saved for the unfinished record (no new call for them).

The cash numbers are the user's decision (lazy-harness SSOT), never inferred here.
"""
import argparse, hashlib, io, json, math
from pathlib import Path

import host_defaults
from safety import STATE_HOME, Stop, inputs_home, load, save

COUNTERS = ('sdk', 'jev_http', 'embedding_post', 'embedding_inputs')


def _num(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise Stop('plan: %s must be a finite non-negative number' % name)
    return float(value)


def check_plan(plan, *, dry):
    """Refuse an under-specified or over-budget plan before any effect."""
    if plan.get('stage') not in ('extract', 'place'):
        raise Stop('plan: stage must be extract or place')
    if not isinstance(plan.get('run'), str) or not plan['run'].strip():
        raise Stop('plan: run name required')
    if not plan.get('records'):
        raise Stop('plan: records required')
    caps = plan.get('caps') or {}
    if set(caps) != set(COUNTERS) or any(type(v) is not int or v < 0 for v in caps.values()):
        raise Stop('plan: caps needs non-negative integer ' + '/'.join(COUNTERS))
    cap = _num(plan.get('cash_cap_usd'), 'cash_cap_usd')
    ceiling = _num(plan.get('absolute_ceiling_usd'), 'absolute_ceiling_usd')
    prior = _num(plan.get('prior_spent_usd'), 'prior_spent_usd')
    _num(plan.get('jev_reserve_usd'), 'jev_reserve_usd')
    if prior + cap > ceiling + 1e-12:
        raise Stop('plan: prior spent + run cash cap exceeds the absolute ceiling (%.6f + %.6f > %.6f)' % (prior, cap, ceiling))
    if not dry and not (isinstance(plan.get('approval'), str) and plan['approval'].strip()):
        raise Stop('plan: a paid run needs the user approval reference')
    if plan['stage'] == 'place' and (not plan.get('extracts') or not plan.get('base')):
        raise Stop('plan: place needs approved extracts and a base snapshot')
    return plan


class Broker:
    """Counts and caps every provider attempt; a dry run refuses the first one."""

    def __init__(self, plan, *, dry, writer_factory=None, worker_factory=None, judgement_http=None,
                 embedding_http=None, embedding_remote=None):
        self.caps = dict(plan['caps']); self.used = {k: 0 for k in COUNTERS}; self.dry = dry
        self.cash_cap = float(plan['cash_cap_usd']); self.reserve = float(plan['jev_reserve_usd']); self.cash = 0.0; self.unknown_cost = 0
        self.uncertain = 0; self.pending = 0.0; self.live = None
        self._writer = writer_factory; self._worker = worker_factory
        self._judgement = judgement_http; self._embed = embedding_http; self._remote = embedding_remote

    def admit(self, kind, n=1, reserve=0.0):
        if self.dry:
            raise Stop('DRYRUN: provider attempt refused (%s)' % kind)
        if self.used[kind] + n > self.caps[kind]:
            raise Stop('cap reached before effect: %s (%d/%d)' % (kind, self.used[kind], self.caps[kind]))
        if self.cash + reserve > self.cash_cap + 1e-12:
            raise Stop('run cash cap reached before effect: $%.6f + reserve $%.6f > $%.6f' % (self.cash, reserve, self.cash_cap))
        self.used[kind] += n
        self._persist()

    def charge(self, usd):
        if type(usd) in (int, float) and math.isfinite(usd) and usd > 0: self.cash += float(usd)
        self._persist()

    def _persist(self):
        if self.live is not None: save(self.live, self.accounting())

    def resume_from(self, prior):
        """Continue a run's counters and cash; an in-flight Jev reserve from a crash counts as spent."""
        self.used = {k: int((prior.get('used') or {}).get(k, 0)) for k in COUNTERS}
        self.cash = float(prior.get('cash_usd', 0.0)) + float(prior.get('pending_reserve_usd', 0.0))
        self.unknown_cost = int(prior.get('unknown_cost_calls', 0)); self.uncertain = int(prior.get('uncertain_calls', 0))

    # --- judgement transport (urlopen shape): count, then read cost from the JSON reply
    def judgement(self, request, timeout=60):
        self.admit('jev_http', reserve=self.reserve)
        send = self._judgement or host_defaults._factory('jev_http', 'send')
        # A sent request whose reply is lost may still be billed: count its reserve as spent and stop, never re-send (#24).
        self.pending = self.reserve; self._persist()
        try:
            with send(request, timeout=timeout) as response:
                raw = response.read()
        except Exception as exc:
            self.pending = 0.0; self.cash += self.reserve; self.uncertain += 1; self._persist()
            raise Stop('Jev request outcome unknown (%s); its reserve $%.6f is counted as spent; stopping without re-send '
                       '(uncertain calls: %d)' % (type(exc).__name__, self.reserve, self.uncertain))
        self.pending = 0.0
        # The provider reports the cost in usage.cost (digest_driver reads the same field). A reply without a known cost
        # stops the run: spending must never continue blind (the first paid run charged 0 because of this, #24).
        try:
            usage = json.loads(raw).get('usage')
        except ValueError:
            usage = None
        cost = usage.get('cost') if isinstance(usage, dict) else None
        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
            self.unknown_cost += 1; self.cash += self.reserve; self._persist()
            raise Stop('Jev reply without a known usage.cost; stopping before more spend (unknown-cost calls: %d)' % self.unknown_cost)
        self.charge(cost)
        return io.BytesIO(raw)

    # --- embedding transports
    def embedding(self, request, timeout=30):
        body = json.loads(request.data or b'{}')
        self.admit('embedding_post'); self.admit('embedding_inputs', len(body.get('texts') or []))
        send = self._embed or host_defaults._factory('embed_http', 'send')
        return send(request, timeout=timeout)

    async def embedding_remote(self, url, headers, body, timeout):
        self.admit('embedding_post'); self.admit('embedding_inputs', len(body.get('input') or []))
        post = self._remote or host_defaults._factory('embed_http', 'remote_post')
        return await post(url, headers, body, timeout)

    # --- writer / worker factories (P4 role ports)
    def _role(self, port):
        def call(role, packet):
            self.admit('sdk')
            return port(role, packet)
        return call

    def writer_factory(self, paid):
        return self._role((self._writer or host_defaults.writer_factory())(paid))

    def worker_factory(self, paid):
        return self._role((self._worker or host_defaults.worker_factory())(paid))

    def install(self):
        host_defaults.install_judgement_http(self.judgement)
        host_defaults.install_embedding_transport(local=self.embedding, remote=self.embedding_remote)

    @staticmethod
    def uninstall():
        host_defaults.install_judgement_http(None)
        host_defaults.install_embedding_transport()

    def accounting(self):
        return {'used': dict(self.used), 'caps': dict(self.caps), 'cash_usd': round(self.cash, 9), 'cash_cap_usd': self.cash_cap,
                'unknown_cost_calls': self.unknown_cost, 'uncertain_calls': self.uncertain, 'pending_reserve_usd': self.pending}


def load_records(plan):
    out = []
    for r in plan['records']:
        text = (STATE_HOME / 'v1' / r['path']).read_text() if (STATE_HOME / 'v1' / r['path']).exists() else (inputs_home() / 'v1' / r['path']).read_text()
        sha = hashlib.sha256(text.encode()).hexdigest()
        if r.get('sha256') and r['sha256'] != sha:
            raise Stop('source drift: ' + r['record_id'])
        out.append({'record_id': r['record_id'], 'path': r['path'], 'sha256': sha, 'text': text})
    return out


def run(plan, *, dry, ports_factory, out_dir=None, broker=None):
    """ports_factory(broker) -> place_write.Ports built on the broker's factories (production or injected).
    Returns {'outcome', 'stage', 'accounting', 'reports'}; a Stop is recorded as the outcome, never swallowed silently."""
    import stagewise as sw
    check_plan(plan, dry=dry)
    broker = broker or Broker(plan, dry=dry)
    out = Path(out_dir or (STATE_HOME / 'runs' / plan['run']))
    out.mkdir(parents=True, exist_ok=True)
    if not dry:
        live, final = out / 'accounting-live.json', out / 'accounting.json'
        prior = load(live) if live.exists() else (load(final).get('accounting') if final.exists() else None)
        if prior is not None:
            if plan.get('resume') is not True:
                result = {'outcome': 'run %s already has accounting; use a new run name or "resume": true (nothing sent)' % plan['run'],
                          'stage': plan['stage'], 'dry_run': dry, 'accounting': prior, 'reports': {}}
                save(out / 'accounting-refused.json', result)
                return result
            broker.resume_from(prior)
        broker.live = live
        broker._persist()
    broker.install()
    reports, outcome = {}, 'completed'
    try:
        ports = ports_factory(broker)
        if plan['stage'] == 'extract':
            for rec in load_records(plan):
                report = sw.run_extract(ports, [rec], host=plan.get('host', 'knowledge-local'))
                save(out / ('stage1-extract-%s.json' % rec['record_id']), report); reports[rec['record_id']] = report['sha']
        else:
            base = load(Path(plan['base']))
            resuming = bool(plan.get('resume')) and not dry
            for rid, path in plan['extracts'].items():
                done = out / ('stage2-place-%s.json' % rid)
                if resuming and done.exists():
                    # #24 runner defect: a resumed run skips records it already finished (no new call).
                    report = load(done)
                else:
                    resume_rows = None
                    if resuming:
                        resume_rows = {}
                        for f in sorted(out.glob('stage2-row-%s-*.json' % rid)):
                            saved = load(f); resume_rows[saved['row']['index']] = saved
                    report = sw.run_place(ports, load(Path(path)), base, second_pass=bool(plan.get('second_pass')), upper_grouping=bool(plan.get('upper_grouping')), one_home=bool(plan.get('one_home')), warrant=bool(plan.get('warrant')), refresh_descriptions=bool(plan.get('refresh_descriptions')), co_grouping=bool(plan.get('co_grouping')),
                                          resume_rows=resume_rows,
                                          on_row=lambda i, row, d, s, g, rid=rid: save(out / ('stage2-row-%s-%03d.json' % (rid, i)), {'row': row, 'domains': [x['name'] for x in d], 'groupings': g}))
                    save(done, report)
                reports[rid] = report['sha']
                # #24 runner defect: upper groupings carry from one record to the next within a run.
                base = sw.snapshot('in-run-after-' + rid, report['domains_after'], report['subjects_after'], parent=base['name'], db_dump=base.get('db_dump'),
                                   groupings=report.get('groupings_after', base.get('groupings')))
    except Stop as exc:
        outcome = str(exc)
    finally:
        broker.uninstall()
    result = {'outcome': outcome, 'stage': plan['stage'], 'dry_run': dry, 'accounting': broker.accounting(), 'reports': reports}
    save(out / ('accounting-dryrun.json' if dry else 'accounting.json'), result)
    return result


def production_ports(plan):
    """Real ports: Jev/embedding through the broker-installed transports, writer/worker through the broker factories."""
    import config, place_write as w
    from safety import Ledger, Paid
    cfg = config.require('jev_api_key', 'jev_base_url', 'jev_model')
    def make(broker):
        ledger = Ledger(STATE_HOME / 'runs' / plan['run'] / 'paid.jsonl', plan['run'], {'generation': 10 ** 6, 'maintenance': 10 ** 6},
                        cash_limit=float(plan['cash_cap_usd']))
        paid = Paid(ledger, 'generation', cfg)
        return w.production(paid, cfg, writer_factory=broker.writer_factory, worker_factory=broker.worker_factory)
    return make


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--plan', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    plan = load(Path(args.plan))
    result = run(plan, dry=args.dry_run, ports_factory=production_ports(plan))
    print(json.dumps(result, ensure_ascii=False))
    ok = result['outcome'] == 'completed' or (args.dry_run and result['outcome'].startswith('DRYRUN'))
    raise SystemExit(0 if ok else 2)


if __name__ == '__main__':
    main()
