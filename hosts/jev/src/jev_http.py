"""Direct Jev HTTP judgement transport (host side). The core builds the request; this performs the exchange."""
from urllib.request import urlopen


def send(request, timeout=60):
    return urlopen(request, timeout=timeout)
