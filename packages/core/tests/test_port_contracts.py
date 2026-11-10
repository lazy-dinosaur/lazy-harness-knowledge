"""Port contracts: shapes, result checks and the core's use of them."""
import offline_guard  # noqa: F401
import inspect, unittest

import ports as pc
import place_write as w
from safety import Stop


def fakes(**over):
    base = dict(model=lambda name, packet, task: {}, encode=lambda texts: [[0., 1.] for _ in texts],
                choose=lambda kind, state, options: {k: 1. / len(options) for k in options},
                ask=lambda state, labels, question: [.5 for _ in labels])
    base.update(over)
    return w.Ports(base['model'], base['encode'], base['choose'], base['ask'], writer=base.get('writer'), worker=base.get('worker'))


class PortContracts(unittest.TestCase):
    def test_fake_ports_conform(self):
        self.assertEqual(pc.conforms(fakes(writer=lambda stage, packet: {}, worker=lambda role, packet: {})), [])

    def test_missing_or_wrong_required_port_is_reported(self):
        import dataclasses
        p = dataclasses.replace(fakes(), ask=None)
        self.assertEqual(pc.conforms(p), ['ask'])

    def test_choice_check_matches_core_behaviour(self):
        opts = {'a': 'A', 'none': 'n'}
        self.assertEqual(pc.check_choice({'a': .2, 'none': .8}, opts), {'a': .2, 'none': .8})
        for bad in ({'a': .2}, {'a': .2, 'none': 1.5}, {'a': float('nan'), 'none': .1}, [.2, .8]):
            with self.assertRaisesRegex(Stop, 'invalid typed probabilities'): pc.check_choice(bad, opts)
            with self.assertRaises(Stop): w.probabilities(bad, opts) if isinstance(bad, dict) else (_ for _ in ()).throw(Stop('x'))

    def test_ask_check_matches_core_behaviour(self):
        self.assertEqual(pc.check_ask([.3], 1), [.3])
        for bad in ([], [1.2], [float('inf')], [True and 'x']):
            with self.assertRaisesRegex(Stop, 'invalid typed Noul score'): pc.check_ask(bad, 1)
        with self.assertRaisesRegex(Stop, 'invalid typed Noul score'):
            w.score(fakes(ask=lambda s, l, q: [2.]), {}, 'q')
        with self.assertRaisesRegex(Stop, 'batched'): pc.check_ask([.1], 2)

    def test_vectors_and_role_results(self):
        self.assertEqual(pc.check_vectors([[0., 1.], [1, 0]], 2), [[0., 1.], [1, 0]])
        for bad in ([[0., 1.]], [[0., 1.], [1.]], [[], []], [[float('nan')], [0.]]):
            with self.assertRaises(Stop): pc.check_vectors(bad, 2)
        self.assertEqual(pc.check_role_result({'ok': True}), {'ok': True})
        with self.assertRaises(Stop): pc.check_role_result('text')

    def test_core_reaches_hosts_only_through_factories(self):
        import host_defaults
        self.assertEqual(sorted(n for n, _ in inspect.getmembers(host_defaults, inspect.isfunction) if not n.startswith('_')),
                         ['embedding_http', 'embedding_remote_post', 'install_embedding_transport', 'install_judgement_http', 'judgement_http', 'lazy_judgement_http', 'worker_factory', 'writer_factory'])


if __name__ == '__main__':
    unittest.main()
