"""p2-s6-1 defect: stage-6 digest must route (identity_ref, no model call) before the partition actor, and stop on drift."""
import offline_guard  # noqa: F401
import unittest
from unittest.mock import patch

import stage_store
from safety import Stop


class Digest(unittest.TestCase):
    def store(self):
        st = stage_store.StageStore.__new__(stage_store.StageStore)
        st.dsn, st.host, st.adapt, st.judge = 'dsn', 'h', {'embed_fn': 'E', 'same': 'S'}, 'J'
        return st

    def test_routes_then_digests_the_placed_domain(self):
        calls = []
        with patch('domain_router.route_pending', lambda dsn, uid, c, f, e: calls.append(('route', uid, c, f, e)) or [('h', '주문')]), \
             patch('digest_driver.run_digestion', lambda *a, **k: calls.append(('digest', k['domain'])) or {'status': 'processed'}):
            out = self.store().digest('u1', '주문')
        self.assertEqual(calls, [('route', 'u1', None, None, 'E'), ('digest', '주문')])
        self.assertEqual(out, {'status': 'processed'})

    def test_stops_when_routing_lands_elsewhere(self):
        with patch('domain_router.route_pending', lambda *a: [('h', '다른')]), patch('digest_driver.run_digestion', lambda *a, **k: {}):
            with self.assertRaises(Stop):
                self.store().digest('u1', '주문')


if __name__ == '__main__':
    unittest.main()
