"""Experiment-only paid-effect journal. No application imports or effects at import time."""
import contextlib
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.request
import urllib.error

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RUNNER = HERE.parent
# Run state never lives inside the source tree (issue #6 step 5): default <repo>/.state (gitignored).
STATE_HOME = Path(os.environ.get('LH_KNOWLEDGE_STATE_HOME') or (ROOT / '.state')).resolve()
# Experiment inputs come from outside the repository; unset means "no experiment inputs available".
_INPUTS = os.environ.get('LH_KNOWLEDGE_INPUTS')
INPUTS_HOME = Path(_INPUTS).resolve() if _INPUTS else None


def inputs_home():
    if INPUTS_HOME is None:
        raise RuntimeError('experiment inputs are not part of this repository; set LH_KNOWLEDGE_INPUTS to their folder')
    return INPUTS_HOME

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

def load(path):
    return json.loads(Path(path).read_text())

def save(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w') as f:
        os.chmod(tmp, 0o600)
        json.dump(value, f, ensure_ascii=False, indent=2, default=str)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

class Stop(BaseException):
    """Not swallowed by production retry/fallback loops."""

class Ledger:
    def __init__(self, path, binding, caps, subscription_limits=None, cash_limit=None):
        self.path, self.binding, self.caps = Path(path), binding, caps
        self.subscription_limits = subscription_limits
        self.cash_limit = cash_limit
        self.lock = threading.RLock()
        self.active = set()
        self.rows = [json.loads(x) for x in self.path.read_text().splitlines()] if self.path.exists() else []
        if any(r['binding'] != binding for r in self.rows):
            raise Stop('ledger binding drift')
    def append(self, row):
        row = {**row, 'binding': self.binding, 'at': time.time()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a') as f:
            os.chmod(self.path, 0o600)
            f.write(json.dumps(row, ensure_ascii=False, default=str) + '\n'); f.flush(); os.fsync(f.fileno())
        self.rows.append(row)
    def _effect_rows(self, ident, step=None):
        rows = [r for r in self.rows if r['id'] == ident or
                (step is not None and r['step'] == step and r.get('imported_from', {}).get('id') == ident)]
        cut = max((i for i, r in enumerate(rows) if r['state'] == 'released'), default=-1)
        return rows[cut + 1:]  # a Main-released uncertain reservation may be requested again
    def spent(self, step, account=None):
        # Released uncertain reservations stay charged (conservative); each effect then adds its latest row after release.
        # One pass (was O(rows^2): ~6s per call on a 12k-row ledger, which blocked the embedding event loop).
        # Same semantics as summing released rows + the latest row after the last release of each effect id.
        total = 0
        groups, idents = {}, []
        for r in self.rows:
            groups.setdefault(r['id'], []).append(r)
            if r['step'] == step and (account is None or r.get('account','cash') == account):
                if r['state'] == 'released': total += r['charge']
                idents.append(r['id'])
        for ident in dict.fromkeys(idents):
            rows = groups[ident]
            cut = max((i for i, x in enumerate(rows) if x['state'] == 'released'), default=-1)
            rows = rows[cut + 1:]
            if rows: total += rows[-1]['charge']
        return total
    def admit_cash(self, reserve):
        if self.cash_limit is not None and sum(self.spent(s,'cash') for s in self.caps) + reserve > self.cash_limit:
            raise Stop('total cash admission cap: $'+str(self.cash_limit))

    def release(self, ident, reason, *, evidence=None, base="https://openrouter.ai/api"):
        """Main-only explicit decision: release an uncertain reservation of an idempotent embedding call."""
        rows = self._effect_rows(ident)
        if not rows or rows[-1]['state'] != 'reserved': raise Stop('only an uncertain reservation can be released')
        if rows[-1]['step'] != 'embedding': raise Stop('release is allowed only for idempotent embedding calls')
        details = {}
        charge = rows[-1]['charge']
        if evidence is not None:
            from jev_failure import uncharged
            free = uncharged(evidence, self.path.parent, base, endpoint='/v1/embeddings')
            charge = 0 if free else charge
            details = {'error_evidence':evidence,
                       'resolution':'embedding-uncharged' if free else 'embedding-conservative-spent'}
        self.append(dict(id=ident, step=rows[-1]['step'], state='released', charge=charge, reason=str(reason)[:300], **details))
    def call(self, step, key, reserve, effect, cost=lambda x: None, *, account="cash", model=None, request=None):
        ident = digest([step, key])
        with self.lock:
            old = self._effect_rows(ident, step)
            if old:
                row = old[-1]
                if row['state'] != 'done': raise Stop('uncertain paid call; no replay: ' + ident)
                result = load(self.path.parent / row['result'])
                if digest(result) != row['sha']: raise Stop('paid result drift')
                return result
            latest = {r['id']: r for r in self.rows}
            if any(r['state'] == 'reserved' and ident_ not in self.active for ident_, r in latest.items()):
                raise Stop('unresolved paid reservation blocks new effects')
            if account == 'subscription' and self.subscription_limits:
                limit=self.subscription_limits[model]
                usage=[r for r in latest.values() if r.get('account')=='subscription' and r.get('model')==model]
                seconds=sum(r.get('seconds',limit['per_call_seconds']) for r in usage)
                if len(usage)+1 > limit['calls'] or seconds+limit['per_call_seconds'] > limit['seconds']:
                    raise Stop('subscription call/time admission limit: '+model)
            elif reserve <= 0 or self.spent(step, 'cash' if self.subscription_limits else None) + reserve > self.caps[step]:
                raise Stop('step admission cap: ' + step)
            if account == 'cash': self.admit_cash(reserve)
            self.append(dict(id=ident, step=step, state='reserved', charge=reserve, account=account, model=model))
            self.active.add(ident)
        start = time.monotonic()
        try:
            result = effect()
            actual = cost(result)
            charge = reserve if actual is None else float(actual)
            if charge < 0: raise ValueError('negative usage')
            with self.lock:
                name = 'responses/' + ident + '.json'
                save(self.path.parent / name, result)
                self.append(dict(id=ident, step=step, state='done', charge=charge, cost_known=actual is not None,
                                 result=name, sha=digest(result), seconds=time.monotonic()-start, account=account, model=model, request=request))
                if not (account == 'subscription' and self.subscription_limits) and (charge > reserve or self.spent(step, 'cash' if self.subscription_limits else None) > self.caps[step]):
                    raise Stop('actual usage exceeded reservation; stop before next call')
            return result
        except Stop:
            raise
        except BaseException as exc:
            if isinstance(exc, urllib.error.HTTPError):
                from jev_failure import capture
                evidence = capture(exc)
                with self.lock:
                    save(self.path.parent / 'errors' / (ident + '.json'), evidence)
            raise Stop('paid call uncertain; reservation retained: ' + ident + ' ' + type(exc).__name__) from exc
        finally:
            with self.lock: self.active.discard(ident)

@contextlib.contextmanager
def exclusive(home=None):
    (home or STATE_HOME).mkdir(parents=True, exist_ok=True)
    with ((home or STATE_HOME) / '.run.lock').open('a') as f:
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise Stop('another scale-04 process owns the ledger')
        yield

class Paid:
    def __init__(self, ledger, step, cfg):
        self.ledger, self.step, self.cfg = ledger, step, cfg
        self.scope = 'initial'
        self.original_open = urllib.request.OpenerDirector.open
    def lookup_generation(self, opener, request, evidence):
        """One administrative GET, never a model retry. Failure leaves the paid reservation uncertain."""
        from urllib.parse import urlsplit
        base = self.cfg['jev_base_url'].rstrip('/')
        generation = evidence.get('generation_id')
        # Only OpenRouter's own endpoint can establish OpenRouter billing; no redirects.
        if not generation or urlsplit(base).hostname != 'openrouter.ai':
            return
        url = base + '/v1/generation?id=' + generation
        lookup = urllib.request.Request(url, headers={'Authorization':request.get_header('Authorization', '')})
        try:
            # Original opener has the standard redirect handler; fail closed before following.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    raise OSError('generation lookup redirect forbidden')
            safe_opener = urllib.request.build_opener(NoRedirect())
            with self.original_open(safe_opener, lookup, timeout=10) as response:
                receipt = {'url':url, 'status':response.status, 'body':json.load(response)}
            save(self.ledger.path.parent / 'jev-generation-evidence' / (generation + '.json'), receipt)
        except Exception:
            return  # 404, timeout, invalid receipt or no generation: NOT proof of zero cost

    def http(self, opener, request, *args, **kwargs):
        url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
        base = self.cfg['jev_base_url'].rstrip('/')
        if url not in (base + '/v1/systemone', base + '/v1/embeddings') or not url.startswith('https://'):
            raise Stop('unapproved HTTP destination')
        body = json.loads(request.data)
        step = 'embedding' if url.endswith('/embeddings') else self.step
        reserve = max(.00001, len(request.data) / 2 * .02 / 1e6) if step == 'embedding' else .002
        def effect():
            with self.original_open(opener, request, *args, **kwargs) as response:
                data = json.load(response)
            if '/systemone' in url:
                if set(data.get('answers', {})) != set(body['questions']): raise ValueError('answer keys')
            else:
                vectors = data.get('data', [])
                if len(vectors) != len(body['input']) or any(len(x['embedding']) != 1024 for x in vectors):
                    raise ValueError('e5-large shape')
            return data
        key = [self.scope, url, body]
        ident = digest([step, key])
        jev = url.endswith('/systemone')
        for attempt in range(3):
            if jev and sum(r.get('resolution') == 'jev-uncharged' for r in self.ledger.rows if r['id'] == ident) >= 2:
                raise Stop('Jev definite failure retry exhausted')
            try:
                data = self.ledger.call(step, key, reserve, effect, lambda r: (r.get('usage') or {}).get('cost'), request={'url':url,'body':body})
                break
            except Stop as stop:
                # Main decision 2026-10-11: an e5-large embedding request is idempotent; a timed-out one is released
                # (its reservation stays charged) and sent again, at most twice. Jev/model calls are never re-sent.
                cause = stop.__cause__
                if jev and self.ledger.cash_limit is not None: raise  # pilot controller owns conservative retries
                if jev and isinstance(cause, urllib.error.HTTPError):
                    from jev_failure import resolve, uncharged
                    evidence = load(self.ledger.path.parent / 'errors' / (ident + '.json'))
                    if not uncharged(evidence, self.ledger.path.parent, base):
                        self.lookup_generation(opener, request, evidence)
                    if uncharged(evidence, self.ledger.path.parent, base):
                        resolve(self.ledger, ident, 'received HTTP failure; matched zero-cost generation lookup', base=base, require_uncharged=True)
                        if sum(r.get('resolution') == 'jev-uncharged' for r in self.ledger.rows if r['id'] == ident) < 2:
                            continue
                        raise Stop('Jev definite failure retry exhausted') from cause
                if step != 'embedding' or attempt == 2 or not isinstance(cause, (TimeoutError, OSError)) or 'uncertain' not in str(stop):
                    raise
                self.ledger.release(digest([step, key]), 'auto: embedding ' + type(cause).__name__ + f' attempt {attempt + 1}')
        return io.BytesIO(json.dumps(data).encode())
    async def embedding_send(self, url, headers, body, attempt, branch):
        """Journal EVERY physical request, including cancelled losers; only embeddings may be released."""
        import asyncio
        if url != self.cfg['jev_base_url'].rstrip('/') + '/v1/embeddings' or not url.startswith('https://'):
            raise Stop('unapproved embedding destination')
        key = [self.scope, url, body, 'hedged-v1', attempt, branch]
        ident = digest(['embedding', key]); ledger = self.ledger
        from embedding_models import profile
        reserve = max(.00001, len(json.dumps(body).encode()) * profile(body['model'])['usd_per_million_tokens'] / 1e6)
        with ledger.lock:
            old = ledger._effect_rows(ident, 'embedding')
            if not old:
                # A previous hedge/resend may already have completed this exact body.
                # Reuse its paid response before issuing a released earlier attempt again.
                for previous_attempt in range(4):
                    for previous_branch in range(2):
                        previous = digest(['embedding', [self.scope, url, body, 'hedged-v1', previous_attempt, previous_branch]])
                        rows = ledger._effect_rows(previous, 'embedding')
                        if rows and rows[-1]['state'] == 'done':
                            old = rows
                            break
                    if old: break
            if old:
                row = old[-1]
                if row['state'] != 'done': raise Stop('uncertain embedding; explicit release required')
                result = load(ledger.path.parent / row['result'])
                if digest(result) != row['sha']: raise Stop('paid result drift')
                return result
            latest = {r['id']: r for r in ledger.rows}
            if any(r['state'] == 'reserved' and i not in ledger.active for i,r in latest.items()):
                raise Stop('unresolved paid reservation blocks new effects')
            budget = getattr(self, 'embedding_budget', None)
            if budget and ledger.spent('embedding') - budget['spent_before'] + reserve > budget['cap']:
                raise Stop('calibration embedding admission cap (< $0.01)')
            if ledger.spent('embedding') + reserve > ledger.caps['embedding']:
                raise Stop('step admission cap: embedding')
            ledger.admit_cash(reserve)
            ledger.append(dict(id=ident,step='embedding',state='reserved',charge=reserve,account='cash'))
            ledger.active.add(ident)
        start = time.monotonic()
        try:
            result = await self.original_embedding_send(url, headers, body, attempt, branch)
            actual = (result.get('usage') or {}).get('cost')
            charge = reserve if actual is None else float(actual)
            if charge < 0: raise ValueError('negative embedding cost')
            with ledger.lock:
                name = 'responses/' + ident + '.json'; save(ledger.path.parent/name, result)
                ledger.append(dict(id=ident,step='embedding',state='done',charge=charge,account='cash',
                    cost_known=actual is not None,result=name,sha=digest(result),seconds=time.monotonic()-start,
                    embedding_request={'url':url,'body':body}))
                if charge > reserve or ledger.spent('embedding') > ledger.caps['embedding']:
                    raise Stop('actual embedding cost exceeded reservation')
            return result
        except (asyncio.CancelledError, Exception) as exc:
            # Received failure != proof of free billing. Persist evidence and use the same generation rule.
            from jev_failure import capture, uncharged
            from embedding_admission import rate_limited
            evidence = capture(exc)
            base = self.cfg['jev_base_url'].rstrip('/')
            if evidence is not None:
                evidence['provider_rate_limit'] = rate_limited(exc)
                save(ledger.path.parent / 'errors' / (ident + '.json'), evidence)
                if rate_limited(exc) and not uncharged(evidence, ledger.path.parent, base, endpoint='/v1/embeddings'):
                    request = urllib.request.Request(url, headers=headers)
                    await asyncio.to_thread(self.lookup_generation, None, request, evidence)
            # Deadlines/losing hedges and unproven HTTP failures retain their full conservative spend.
            with ledger.lock:
                ledger.release(ident, 'embedding-only bounded retry/hedge: ' + type(exc).__name__, evidence=evidence, base=base)
            raise
        finally:
            with ledger.lock: ledger.active.discard(ident)

    def __enter__(self):
        import embedding_transport
        self.original_embedding_send = embedding_transport.send
        embedding_transport.send = self.embedding_send
        owner = self
        urllib.request.OpenerDirector.open = lambda opener, req, *a, **kw: owner.http(opener, req, *a, **kw)
        return self
    def __exit__(self, *exc):
        import embedding_transport
        embedding_transport.send = self.original_embedding_send
        urllib.request.OpenerDirector.open = self.original_open
    def model(self, prompt, cwd, *, sol=False, reserve=.10, step=None):
        model = 'openai/gpt-6-sol:medium' if sol else 'openai/gpt-6-luna:medium'
        # Bind all files the read-only model is allowed to see, not transient path names.
        files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(cwd).iterdir() if p.is_file()}
        def effect():
            result = subprocess.run(['pi','-p','--mode','json','--model',model,'--tools','read',
                '--no-extensions','--no-skills','--no-session',prompt], cwd=cwd, capture_output=True, text=True, timeout=900)
            turns, text = [], ''
            for line in result.stdout.splitlines():
                try: event = json.loads(line)
                except ValueError: continue
                if event.get('type') != 'turn_end': continue
                message = event.get('message') or {}
                if message.get('role') == 'assistant':
                    turns.append(message.get('usage'))
                    parts = [p.get('text','') for p in message.get('content',[]) if p.get('type') == 'text']
                    if parts: text = '\n'.join(parts)
            if result.returncode or not text or not turns: raise ValueError('model failed/empty')
            if any(not isinstance(u,dict) or not isinstance((u.get('cost') or {}).get('total'), (int,float)) for u in turns):
                raise ValueError('unknown all-turn cost')
            return {'text':text,'cost':sum(u['cost']['total'] for u in turns), 'turns':turns}
        value = self.ledger.call(step or self.step,[self.scope,model,prompt.replace(str(cwd), '<packet-dir>'),files],reserve,effect,lambda r:r['cost'],
                                 account='subscription',model='Sol' if sol else 'Luna')
        return value['text'], {'usageKnown':True,'usage':{'cost_usd':value['cost'],'turns':len(value['turns'])}}
