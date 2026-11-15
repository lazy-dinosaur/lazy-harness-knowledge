"""Stage 3: a new subject may not take a name or alias held by a subject in another domain (p2-warm-4: three `병원`)."""
import offline_guard  # noqa: F401
import dataclasses
import inspect
import unittest
from unittest.mock import patch

import place_write as w
import stagewise as sw


def ports(**kw):
    return w.Ports(lambda n, pk, t: {}, lambda xs: [[0., 1.]] * len(xs), lambda k, s, o: {x: 1. / len(o) for x in o}, lambda s, l, q: [.1], **kw)


class SubjectNameTaken(unittest.TestCase):
    def test_helper_normalizes(self):
        p = ports(taken_subject_names=frozenset({w._norm('병원'), w._norm('hospital')}))
        self.assertTrue(w.subject_name_taken('병원', p))
        self.assertTrue(w.subject_name_taken(' Hospital ', p))
        self.assertFalse(w.subject_name_taken('공급 단위', p))
        self.assertFalse(w.subject_name_taken('병원', ports()))

    def test_creation_path_holds_only_a_new_subject(self):
        self.assertIn('taken_subject_names', {f.name for f in dataclasses.fields(w.Ports)})
        src = inspect.getsource(w._place_identity_legacy)
        self.assertIn("kind=='subject' and not existing and subject_name_taken(name, ports)", src)
        self.assertIn('E_SUBJECT_NAME_TAKEN', src)

    def test_run_place_passes_other_domains_names_for_the_subject_step(self):
        domains = [{'name': '계정', 'description': 'a'}, {'name': '설정', 'description': 'b'}]
        subjects = {'계정': [{'name': '병원', 'subject_id': 'h', 'description': 'h', 'aliases': ['hospital']}],
                    '설정': [{'name': '공급 단위', 'subject_id': 'u', 'description': 'u', 'aliases': []}]}
        rows = [{'record_id': 'R', 'index': 0, 'flags': [], 'unit': {'unit_id': 'u0', 'content': 'c', 'target_expression': 'Unit configuration', 'source_ranges': [[1, 1]]}}]
        extract = sw.approve(sw._sealed({'version': sw.VERSION, 'stage': 'extract', 'host': 'h', 'records': [], 'sources': {'R': '줄\n'}, 'rows': rows}), 'test')
        seen = {}
        def place(kind, unit, domain, catalog, p, k, document=None):
            seen[kind] = p.taken_subject_names
            if kind == 'domain':
                return {'chosen': domains[1], 'new': False}
            return {'chosen': catalog[0], 'new': False}
        with patch.object(sw.w, 'place_identity', place):
            sw.run_place(ports(), extract, sw.snapshot('b', domains, subjects))
        self.assertEqual(seen['domain'], frozenset())
        self.assertEqual(seen['subject'], frozenset({w._norm('병원'), w._norm('hospital')}))  # other domain only, not 설정's own


if __name__ == '__main__':
    unittest.main()
