"""Default model transports come from an installed host adapter (hosts/pi), never imported statically by the core.

Callers may always inject writer_factory / worker_factory instead."""
import importlib


class HostUnavailable(RuntimeError):
    pass


def _factory(module, name):
    try:
        return getattr(importlib.import_module(module), name)
    except ImportError:
        raise HostUnavailable(
            'no host adapter on the import path (%s); add hosts/pi/src or inject %s' % (module, name.replace('make_', '') + '_factory')
        ) from None


def writer_factory():
    return _factory('writer_transport', 'make_writer')


def worker_factory():
    return _factory('active_worker_transport', 'make_worker')


_installed_judgement_http = None


def install_judgement_http(send):
    """The composition layer supplies judgement transport: send(request, timeout=...) -> context manager yielding a
    file-like JSON response (urllib.request.urlopen shape). Pass None to fall back to the host adapter by name."""
    global _installed_judgement_http
    _installed_judgement_http = send


def judgement_http():
    """Judgement transport, resolved at call time: installed > hosts/jev (``jev_http.send``) > HostUnavailable."""
    return _installed_judgement_http or _factory('jev_http', 'send')


def lazy_judgement_http(*args, **kwargs):
    """Call-time resolution so building a judge never needs a host; only sending does."""
    return judgement_http()(*args, **kwargs)
