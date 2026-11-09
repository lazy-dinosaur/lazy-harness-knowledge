"""Embedding-only cancellable HTTP: 30s deadlines, 10s hedge, three resends.
httpx is imported only for a remote request; local/fake embedding has no dependency.
All tasks (including the losing hedge) are cancelled and joined before returning.
"""
import asyncio
import math
import os
from contextvars import ContextVar
from embedding_models import format_inputs, remote_model, profile
from embedding_admission import AdaptiveLimiter, rate_limited, RESTORE_SUCCESSES

_LIMITER = ContextVar("embedding_limiter", default=None)
_ADAPTIVE_STATE = {}


def env_seconds(name, default):
    value = float(os.environ.get(name, default))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(name + ' must be finite and positive seconds')
    return value


TIMEOUT = env_seconds('LH_EMBED_TIMEOUT', 30.0)
HEDGE_AFTER = env_seconds('LH_EMBED_HEDGE_AFTER', 10.0)
RESENDS = 3
# Shared conservative envelope: the local E5 service accepts at most 64 texts.
# Do not assume a larger remote-provider cap without new verified evidence.
MAX_BATCH_INPUTS = 64


def env_positive_int(name, default, maximum=None):
    raw = str(os.environ.get(name, default)).strip()
    if not raw.isascii() or not raw.isdigit() or int(raw) < 1:
        raise ValueError(name + ' must be a positive integer')
    value = int(raw)
    if maximum is not None and value > maximum:
        raise ValueError(name + ' exceeds E5 adapter input cap ' + str(maximum))
    return value


BATCH_SIZE = env_positive_int('LH_EMBED_BATCH_SIZE', 64, MAX_BATCH_INPUTS)
PARALLEL = env_positive_int('LH_EMBED_PARALLEL', 16)


def policy():
    return {'timeout_seconds':TIMEOUT, 'hedge_after_seconds':HEDGE_AFTER, 'resends':RESENDS,
            'batch_size':BATCH_SIZE, 'parallel_batches':PARALLEL, 'batch_input_cap':MAX_BATCH_INPUTS,
            'parallel_limit_scope':'physical_requests_including_hedges',
            'model':profile(remote_model()),
            'adaptive':{'minimum':1, 'rate_limit_factor':0.5, 'restore_after_successes':RESTORE_SUCCESSES,
                        'restore_step':1, 'retry_after':True, 'fallback_backoff_seconds':[1,30]}}


async def send(url, headers, body, attempt, branch):
    import httpx
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
        response = await client.post(url, headers=headers, json=body)
        response.raise_for_status()
        value = response.json()
        # Some providers return an explicit rate-limit error in a successful HTTP envelope.
        error = httpx.HTTPStatusError('provider error envelope', request=response.request, response=response)
        if rate_limited(error):
            raise error
    # Providers may echo the bare model name without the OpenRouter vendor prefix
    # (Perplexity returns 'pplx-embed-v1-0.6b' for 'perplexity/pplx-embed-v1-0.6b').
    if value.get('model', body['model']) not in (body['model'], body['model'].split('/', 1)[-1]):
        from embedding_models import ModelMismatch
        raise ModelMismatch('provider returned a different embedding model')
    data = sorted(value['data'], key=lambda item: item['index'])
    if [x['index'] for x in data] != list(range(len(body['input']))) or any(len(x['embedding']) != 1024 for x in data):
        raise ValueError('remote embedding shape (1024 dimensions required)')
    value['data'] = data
    return value


def retryable(exc):
    import httpx
    return rate_limited(exc) or isinstance(exc, (TimeoutError, OSError, httpx.TransportError)) or (
        isinstance(exc, httpx.HTTPStatusError) and (exc.response.status_code == 429 or exc.response.status_code >= 500))


async def hedged(url, headers, body):
    last = None
    limiter = _LIMITER.get() or AdaptiveLimiter(PARALLEL)
    for attempt in range(RESENDS + 1):
        started = asyncio.Event()
        winner = []
        async def one(branch):
            epoch = await limiter.acquire()
            error = None
            if branch == 0:
                started.set()
            try:
                if winner:
                    # A hedge queued behind the cap must not send after another branch has already won.
                    error = asyncio.CancelledError()  # no physical success/failure to count
                    return winner[0]
                # Queue/Retry-After time is not network time. Every physical branch has its own deadline.
                value = await asyncio.wait_for(send(url, headers, body, attempt, branch), TIMEOUT)
                if not winner:
                    winner.append(value)
                return value
            except BaseException as exc:
                error = exc
                raise
            finally:
                await limiter.finish(epoch, error)
        tasks = [asyncio.create_task(one(0))]
        try:
            await started.wait()
            done, _ = await asyncio.wait(tasks, timeout=HEDGE_AFTER)
            if not done:
                tasks.append(asyncio.create_task(one(1)))
            pending = set(tasks)
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    try:
                        return task.result()
                    except Exception as exc:
                        if not retryable(exc):
                            raise
                        last = exc
        finally:
            for task in tasks:
                if not task.done(): task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    raise TimeoutError('embedding exhausted initial attempt + three resends') from last


async def batches(texts, role, url, headers, model, on_batch=None, *, limiter=None):
    if role not in ('query', 'passage') or not 1 <= BATCH_SIZE <= MAX_BATCH_INPUTS:
        raise ValueError('invalid E5 role/batch size')
    limiter = limiter or AdaptiveLimiter(PARALLEL)
    token = _LIMITER.set(limiter)
    async def batch(chunk):
        value = await hedged(url, headers, {'model': model, 'input': format_inputs(model, chunk, role)})
        vectors = [x['embedding'] for x in value['data']]
        if on_batch is not None:
            on_batch(chunk, vectors)  # persist each success, even if another batch fails
        return vectors
    tasks = [asyncio.create_task(batch(texts[i:i+BATCH_SIZE])) for i in range(0, len(texts), BATCH_SIZE)]
    try:
        return [v for chunk in await asyncio.gather(*tasks) for v in chunk]
    finally:
        for task in tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        _LIMITER.reset(token)


def encode(texts, role, url, headers, model, on_batch=None):
    key = (url, model, PARALLEL)
    limiter = AdaptiveLimiter(PARALLEL, _ADAPTIVE_STATE.get(key))
    try:
        return asyncio.run(batches(texts, role, url, headers, model, on_batch, limiter=limiter))
    finally:
        # Preserve reductions/cooldown across query→passage and subsequent cache misses in this process.
        _ADAPTIVE_STATE[key] = limiter.snapshot()
