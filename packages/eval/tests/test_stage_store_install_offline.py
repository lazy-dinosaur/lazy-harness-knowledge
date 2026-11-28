"""p2-s6-4 dry run: a `persisted` approved-catalog subject absent from this module DB is registered under its fixed id."""
import offline_guard  # noqa: F401
import unittest
from unittest.mock import patch

import stage_store


class Cur:
    def __init__(self, ids): self.ids = ids
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, *a): pass
    def fetchall(self): return [(i,) for i in self.ids]


class Conn:
    def __init__(self, ids): self.ids = ids
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def cursor(self): return Cur(self.ids)


class Install(unittest.TestCase):
    def test_absent_persisted_subject_becomes_allocated_present_one_stays(self):
        st = stage_store.StageStore.__new__(stage_store.StageStore); st.dsn, st.host = 'dsn', 'h'
        rows = [{'unit_id': 'u1', 'fact': {}, 'domain': {'name': 'd'}, 'subject': {'name': 'a', 'subject_id': 'S1', 'identity_origin': 'persisted'}},
                {'unit_id': 'u2', 'fact': {}, 'domain': {'name': 'd'}, 'subject': {'name': 'b', 'subject_id': 'S2', 'identity_origin': 'persisted'}}]
        seen = {}
        def fake(dsn, host, active, encode, **kw):
            seen.update({a['unit_id']: a['subject']['chosen']['identity_origin'] for a in active}); return {}, {}
        with patch('store_pg.connect', lambda dsn: Conn(['S2'])), patch('place_write_absorb.install_rows', fake):
            self.assertEqual(st.install(rows), {})
        self.assertEqual(seen, {'u1': 'allocated', 'u2': 'persisted'})
        self.assertEqual(rows[0]['subject']['identity_origin'], 'persisted')  # caller rows untouched


if __name__ == '__main__':
    unittest.main()
