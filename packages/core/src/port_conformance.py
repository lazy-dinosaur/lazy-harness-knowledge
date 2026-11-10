"""Behaviour-level conformance kit for role ports (writer/worker), owned by the core (issue #14 step 3).

Any implementation of ``writer_factory``/``worker_factory`` — the Pi host, or the composition layer's own backend —
runs these scenarios. The implementation supplies only a wiring shim::

    make_port(paid, respond) -> port

where ``respond(request) -> ('ok', text) | ('fail', code)`` is called once per physical model request and the shim
turns it into its own transport response. The core's ledger (``safety.Ledger``) is the accounting authority.

Scenarios (each raises AssertionError with a reason on violation):

1. success: the port returns the parsed object (``check_role_result``) and the ledger holds a ``done`` row for the call;
2. accounting first: no physical request is sent before the ledger reserved it;
3. uncertain failure: a failed request is never re-sent; a second call with the same input stops without a request.
"""
import tempfile
from pathlib import Path
from types import SimpleNamespace

import ports
from safety import Ledger

CAPS = {'generation': 10, 'maintenance': 10}


def _paid(home, scope):
    return SimpleNamespace(scope=scope, ledger=Ledger(Path(home) / 'paid.jsonl', 'conformance', dict(CAPS)))


def check_success(make_port, role, packet, text):
    with tempfile.TemporaryDirectory() as tmp:
        paid = _paid(tmp, 'ok-' + role); seen = []
        def respond(request):
            reserved = [r for r in paid.ledger.rows if r['state'] == 'reserved']
            assert reserved, 'physical request sent before the ledger reserved it'
            seen.append(request); return ('ok', text)
        result = make_port(paid, respond)(role, packet)
        ports.check_role_result(result)
        assert len(seen) == 1, 'expected exactly one physical request, got %d' % len(seen)
        done = [r for r in paid.ledger.rows if r['state'] == 'done']
        assert done and done[-1]['step'] in CAPS, 'no done ledger row for the call'
        return result


def check_uncertain_not_replayed(make_port, role, packet, code='E_TRANSPORT'):
    with tempfile.TemporaryDirectory() as tmp:
        paid = _paid(tmp, 'fail-' + role); seen = []
        def respond(request):
            seen.append(request); return ('fail', code)
        port = make_port(paid, respond)
        for attempt in (1, 2):
            try:
                port(role, packet)
            except (KeyboardInterrupt, GeneratorExit):
                raise
            except BaseException:  # the core's Stop is not an Exception subclass
                pass
            else:
                raise AssertionError('failed request returned a result on attempt %d' % attempt)
        assert len(seen) == 1, 'uncertain request was re-sent (%d physical requests)' % len(seen)


def run(make_port, cases):
    """cases: iterable of (role, packet, ok_text). Returns the list of parsed results."""
    out = []
    for role, packet, text in cases:
        out.append(check_success(make_port, role, packet, text))
        check_uncertain_not_replayed(make_port, role, packet)
    return out
