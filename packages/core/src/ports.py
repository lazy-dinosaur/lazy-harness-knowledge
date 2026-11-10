"""Port contracts of the knowledge core (issue #1 P4).

The core never imports a host. Every external capability reaches it through one of these ports; a host (hosts/pi) or the
composition layer (lazy-harness, through its own orchestration layer) implements them. The shapes are owned here and do not follow
any host API: when a host API changes, the host implementation absorbs it.

Ports (all synchronous callables):

- ``model(name, packet, task) -> dict``          legacy free-form model call (injected fakes only; production uses ``worker``)
- ``encode(texts) -> list[vector]``               one finite float vector per text, same order
- ``choose(kind, state, options) -> {key: p}``    typed Choice: exactly the option keys, each probability in [0, 1]
- ``ask(state, labels, question) -> [p, ...]``    typed Noul: one probability in [0, 1] per label, same order
- ``writer(stage, packet) -> dict``               role-bound writer call (stage: 'write' | 'rewrite')
- ``worker(role, packet) -> dict``                role-bound worker call (extract, reextract, domain-name, ...)

Judgement transport (resolved at call time by ``host_defaults.judgement_http``): the core builds every Jev request and
never performs that network exchange itself. ``send(request, timeout=...)`` returns a context manager yielding the JSON
response (``urllib.request.urlopen`` shape). Order: a transport installed by the composition layer
(``host_defaults.install_judgement_http``) > the Jev host (``hosts/jev``, ``jev_http.send``) > ``HostUnavailable``.

Embedding transport (``host_defaults.embedding_http`` / ``embedding_remote_post``): the core builds embedding requests
and validates the vectors; the local E5 exchange and the remote provider exchange belong to ``hosts/embed`` unless the
composition layer installs its own (``host_defaults.install_embedding_transport``).

Factories a host provides (``host_defaults`` finds them by module name, or callers inject them):

- ``writer_factory(paid) -> writer``
- ``worker_factory(paid) -> worker``

``paid`` is the core's accounting handle (``safety.Paid``); the factory must charge every effectful call through it.
"""
import math
from typing import Callable, Protocol, runtime_checkable

from safety import Stop


@runtime_checkable
class ModelPort(Protocol):
    def __call__(self, name: str, packet: dict, task: str) -> dict: ...


@runtime_checkable
class EncodePort(Protocol):
    def __call__(self, texts: list) -> list: ...


@runtime_checkable
class ChoosePort(Protocol):
    def __call__(self, kind: str, state: dict, options: dict) -> dict: ...


@runtime_checkable
class AskPort(Protocol):
    def __call__(self, state: dict, labels: list, question: str) -> list: ...


@runtime_checkable
class RolePort(Protocol):
    """writer(stage, packet) and worker(role, packet) share this shape."""
    def __call__(self, role: str, packet: dict) -> dict: ...


WriterFactory = Callable[[object], RolePort]
WorkerFactory = Callable[[object], RolePort]


def _probability(v):
    return type(v) in (float, int) and math.isfinite(v) and 0 <= v <= 1


def check_choice(scores, options):
    """Typed Choice result: exactly the option keys, each a probability."""
    if not isinstance(scores, dict) or set(scores) != set(options) or not all(_probability(v) for v in scores.values()):
        raise Stop('invalid typed probabilities')
    return scores


def check_ask(values, n):
    """Typed Noul result: one probability per label."""
    if not isinstance(values, list) or len(values) != n or not all(_probability(v) for v in values):
        raise Stop('invalid typed Noul score' if n == 1 else 'invalid batched typed Noul scores')
    return values


def check_vectors(vectors, n):
    """Encode result: n non-empty finite float vectors of one dimension."""
    if (not isinstance(vectors, list) or len(vectors) != n
            or any(not isinstance(v, list) or not v or any(type(x) not in (float, int) or not math.isfinite(x) for x in v) for v in vectors)
            or len({len(v) for v in vectors}) > 1):
        raise Stop('invalid embedding vectors')
    return vectors


def check_role_result(value):
    """writer/worker result: a JSON object."""
    if not isinstance(value, dict):
        raise Stop('invalid role result')
    return value


def conforms(ports):
    """Names of required ports a ``place_write.Ports`` value is missing or that do not have the contract shape."""
    required = {'model': ModelPort, 'encode': EncodePort, 'choose': ChoosePort, 'ask': AskPort}
    optional = {'writer': RolePort, 'worker': RolePort, 'ground': AskPort}
    bad = [k for k, proto in required.items() if not isinstance(getattr(ports, k, None), proto)]
    bad += [k for k, proto in optional.items() if getattr(ports, k, None) is not None and not isinstance(getattr(ports, k), proto)]
    return bad
