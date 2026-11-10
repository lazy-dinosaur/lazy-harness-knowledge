"""The conformance kit itself catches broken role-port implementations (fake ports, no host)."""
import offline_guard  # noqa: F401
import unittest

import port_conformance as kit


def honest(paid, respond):
    def port(role, packet):
        key = [paid.scope, role, repr(sorted(packet.items()))]
        def effect():
            kind, value = respond({'role': role})
            if kind != 'ok': raise RuntimeError(value)
            return {'text': value, 'cost': 0}
        r = paid.ledger.call('generation', key, .1, effect, lambda x: x['cost'])
        return {'text': r['text']}
    return port


def sends_first(paid, respond):
    def port(role, packet):
        kind, value = respond({'role': role})  # physical request before accounting
        return paid.ledger.call('generation', [role], .1, lambda: {'text': value, 'cost': 0}, lambda x: x['cost'])
    return port


def retries(paid, respond):
    def port(role, packet):
        for _ in range(2):
            kind, value = respond({'role': role})
            if kind == 'ok': return {'text': value}
        raise RuntimeError('failed')
    return port


class ConformanceKit(unittest.TestCase):
    def test_honest_port_passes(self):
        self.assertEqual(kit.run(honest, [('extract', {'a': 1}, 'x')]), [{'text': 'x'}])

    def test_request_before_accounting_is_caught(self):
        with self.assertRaisesRegex(AssertionError, 'before the ledger reserved'):
            kit.check_success(sends_first, 'extract', {'a': 1}, 'x')

    def test_replayed_uncertain_request_is_caught(self):
        with self.assertRaisesRegex(AssertionError, 're-sent'):
            kit.check_uncertain_not_replayed(retries, 'extract', {'a': 1})


if __name__ == '__main__':
    unittest.main()
