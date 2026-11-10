"""The Pi host's factories satisfy the core's writer/worker port contract (fake inspect/send, no Pi, no network)."""
import unittest

import ports as pc
import writer_transport
import active_worker_transport


class Paid:
    """Minimal stand-in for safety.Paid: records charges, never spends."""
    scope = 'test'
    def __init__(self): self.calls = []
    def call(self, step, key, reserve, effect, charge=None):
        self.calls.append((step, key)); return effect()


class PiPortConformance(unittest.TestCase):
    def test_factories_return_role_ports(self):
        for factory in (writer_transport.make_writer, active_worker_transport.make_worker):
            port = factory(Paid(), inspect=lambda: {'ok': True, 'inputBudget': 1000000}, send=lambda request: {'ok': True, 'text': '{}', 'cost': 0})
            self.assertIsInstance(port, pc.RolePort, factory.__name__)

    def test_host_defaults_resolve_these_factories(self):
        import host_defaults
        self.assertIs(host_defaults.writer_factory(), writer_transport.make_writer)
        self.assertIs(host_defaults.worker_factory(), active_worker_transport.make_worker)


if __name__ == '__main__':
    unittest.main()
