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
