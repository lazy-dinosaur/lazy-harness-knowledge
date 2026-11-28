"""p2-s6-2 defect: stage-6 record / digest run inside the unit-first scope (worktime rejected every fact with E_SUBJECT_TYPE)."""
import offline_guard  # noqa: F401
import unittest
from unittest.mock import patch

import stage_store
import subject_contract as sc


class UnitFirstScope(unittest.TestCase):
    def store(self):
        st = stage_store.StageStore.__new__(stage_store.StageStore)
        st.dsn, st.host, st.adapt, st.judge = 'dsn', 'h', {'embed_fn': 'E', 'same': 'S'}, 'J'
        st.paid = type('P', (), {'scope': None})()
        return st

    def test_digest_runs_in_unit_first_scope(self):
        seen = []
        with patch('domain_router.route_pending', lambda *a: seen.append(('route', sc._UNIT_FIRST.get())) or [('h', 'd')]), \
             patch('digest_driver.run_digestion', lambda *a, **k: seen.append(('digest', sc._UNIT_FIRST.get())) or {}):
            self.store().digest('u', 'd')
        self.assertEqual(seen, [('route', True), ('digest', True)])
        self.assertFalse(sc._UNIT_FIRST.get())

    def test_record_runs_worktime_in_unit_first_scope(self):
        seen = []
        class Cur:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def execute(s, *a): pass
            def fetchone(s): return ('completed',)
        class Conn(Cur):
            def cursor(s): return Cur()
        with patch('place_write_absorb.record_unit', lambda *a, **k: {'entry_id': 'e1'}), \
             patch('worktime_driver.run_worktime', lambda *a, **k: seen.append(sc._UNIT_FIRST.get())), \
             patch('store_pg.connect', lambda dsn: Conn()):
            self.assertEqual(self.store().record('u', {'fact': 'x'}), 'e1')
        self.assertEqual(seen, [True])


if __name__ == '__main__':
    unittest.main()
