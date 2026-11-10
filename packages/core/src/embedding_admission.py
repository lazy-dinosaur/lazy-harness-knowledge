"""Embedding physical-request admission. Existing requests drain on a reduced limit.
Only a new admission must fit the current target; cancelling already-paid requests to
meet a newly lowered target would create extra uncertain charges.
"""
import asyncio
from datetime import timezone
from email.utils import parsedate_to_datetime
import json
import math
import time

RESTORE_SUCCESSES = 8
RATE_CODES = {'429', 'rate_limit', 'rate_limited', 'rate_limit_error', 'rate_limit_exceeded',
              'too_many_requests', 'resource_exhausted'}


def rate_limited(error):
    import httpx
    if not isinstance(error, httpx.HTTPStatusError):
        return False
    response = error.response
    if response.status_code == 429:
        return True
    try:
        value = response.json()
    except (ValueError, UnicodeError):
        return False
    failure = value.get('error') if isinstance(value, dict) else None
    for _ in range(2):  # one typed provider error wrapped by OpenRouter metadata.raw
        if not isinstance(failure, dict):
            return False
        if any(str(failure.get(k, '')).lower() in RATE_CODES for k in ('code', 'type')):
            return True
        metadata = failure.get('metadata')
        raw = metadata.get('raw') if isinstance(metadata, dict) else None
        try:
            nested = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            return False
        failure = nested.get('error') if isinstance(nested, dict) else None
    return False


def retry_after(error):
    response = getattr(error, 'response', None)
    raw = response.headers.get('Retry-After') if response is not None else None
    if raw is None:
        return None
    try:
        seconds = float(raw)
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
    except (ValueError, TypeError):
        pass
    try:
        date = parsedate_to_datetime(raw)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0., date.timestamp() - time.time())
    except (ValueError, TypeError, OverflowError):
        return None


class AdaptiveLimiter:
    def __init__(self, maximum, previous=None):
        if type(maximum) is not int or maximum < 1:
            raise ValueError('positive concurrency required')
        self.maximum = maximum
        previous = previous or {}
        self.limit = min(maximum, max(1, previous.get('limit', maximum)))
        self.successes = previous.get('successes', 0)
        self.blocked_until = previous.get('blocked_until', 0.)
        self.streak = previous.get('streak', 0)
        self.active = 0
        self.epoch = 0
        self.condition = asyncio.Condition()

    async def acquire(self):
        async with self.condition:
            while True:
                delay = self.blocked_until - time.monotonic()
                if delay <= 0 and self.active < self.limit:
                    self.active += 1
                    return self.epoch
                if delay > 0:
                    try:
                        await asyncio.wait_for(self.condition.wait(), delay)
                    except TimeoutError:
                        pass
                else:
                    await self.condition.wait()

    async def finish(self, epoch, error=None):
        async with self.condition:
            self.active -= 1
            if rate_limited(error):
                self.limit = max(1, self.limit // 2)
                self.epoch += 1
                self.successes = 0
                self.streak = min(6, self.streak + 1)
                delay = retry_after(error)
                if delay is None:
                    delay = min(30., 2. ** (self.streak - 1))
                self.blocked_until = max(self.blocked_until, time.monotonic() + delay)
            elif epoch == self.epoch and not isinstance(error, asyncio.CancelledError):
                if error is None:
                    self.successes += 1
                    if self.successes >= RESTORE_SUCCESSES:
                        self.limit = min(self.maximum, self.limit + 1)
                        self.successes = 0
                        self.streak = 0
                else:
                    self.successes = 0
            self.condition.notify_all()

    def snapshot(self):
        return {k:getattr(self, k) for k in ('limit', 'successes', 'blocked_until', 'streak')}
