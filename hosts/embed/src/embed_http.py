"""Embedding transport (host side): the local E5 service exchange and the remote provider exchange.
The core builds requests and validates results; only this module talks to the network."""
from urllib.request import urlopen

from embedding_admission import rate_limited


def send(request, timeout=30):
    return urlopen(request, timeout=timeout)


async def remote_post(url, headers, body, timeout):
    import httpx
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.post(url, headers=headers, json=body)
        response.raise_for_status()
        value = response.json()
        # Some providers return an explicit rate-limit error in a successful HTTP envelope.
        error = httpx.HTTPStatusError('provider error envelope', request=response.request, response=response)
        if rate_limited(error):
            raise error
    return value
