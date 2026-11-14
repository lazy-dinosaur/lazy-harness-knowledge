"""p2-domain-place-4 defect: a new domain may not take the name of an existing upper grouping."""
import offline_guard  # noqa: F401
import dataclasses
import unittest
from unittest.mock import patch

import place_write as w
import stagewise as sw


class ReservedNames(unittest.TestCase):
    def test_ports_carry_reserved_names_and_stagewise_passes_grouping_names(self):
        self.assertIn('reserved_names', {f.name for f in dataclasses.fields(w.Ports)})
        doc = '줄 1\n'
        rows = [{'record_id': 'R', 'index': 0, 'flags': [], 'unit': {'unit_id': 'u0', 'content': '줄 1', 'target_expression': 'x', 'source_ranges': [[1, 1]]}}]
        extract = sw.approve(sw._sealed({'version': sw.VERSION, 'stage': 'extract', 'host': 'h', 'records': [], 'sources': {'R': doc}, 'rows': rows}), 'test')
        base = sw.snapshot('b', [{'name': '예약', 'description': '예약'}], {}, groupings=[{'name': '온보딩', 'description': 'g', 'members': ['예약', 'x']}])
        seen = []
        def place(kind, unit, domain, catalog, p, k, document=None):
            seen.append(p.reserved_names)
            if kind == 'domain':
                return {'chosen': catalog[0], 'new': False}
            return {'chosen': {'name': 's', 'subject_id': 's'}, 'new': True}
        p = w.Ports(lambda n, pk, t: {}, lambda xs: [[0., 1.]] * len(xs), lambda k, s, o: {x: 1. / len(o) for x in o}, lambda s, l, q: [.1])
        with patch.object(sw.w, 'place_identity', place):
            sw.run_place(p, extract, base, upper_grouping=True)
        self.assertEqual(seen[0], frozenset({'온보딩'}))

    def test_creation_refuses_a_reserved_name(self):
        import inspect
        src = inspect.getsource(w._place_identity_legacy)
        self.assertIn('E_NAME_TAKEN', src)


if __name__ == '__main__':
    unittest.main()
