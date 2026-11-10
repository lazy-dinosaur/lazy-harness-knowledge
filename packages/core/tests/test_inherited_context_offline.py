"""#24 source-meaning audit: a list item keeps its parent list introducer (e.g. 'Must not:') verbatim."""
import offline_guard  # noqa: F401
import unittest

import place_write as w

DOC = '''# 주문 규칙

- Must:
  - 취소는 결제 전에만 허용한다
- Must not:
  - 결제된 주문을 삭제한다
  - 다른 고객의 주문을 보여 준다
- 예외 사항:
  - 관리자 화면:
    - 전체 주문을 볼 수 있다

주문 번호는 날짜로 시작한다.
'''


def item(line, content=None):
    text = DOC.splitlines()[line - 1]
    return {'content': content if content is not None else text.strip(), 'target_expression': '주문', 'source_ranges': [[line, line]]}


class InheritedContext(unittest.TestCase):
    def test_prohibition_introducer_is_kept_verbatim(self):
        out = w.inherit_list_introducers(item(6), DOC)
        self.assertEqual(out['source_ranges'], [[5, 5], [6, 6]])
        self.assertEqual(out['content'], '- Must not:\n  - 결제된 주문을 삭제한다')
        self.assertIsNone(w.extraction_content_error(out, DOC))

    def test_bare_item_text_without_list_marker_is_also_expanded(self):
        out = w.inherit_list_introducers(item(7, '다른 고객의 주문을 보여 준다'), DOC)
        self.assertEqual(out['source_ranges'], [[5, 5], [7, 7]]); self.assertIn('Must not:', out['content'])
        self.assertIsNone(w.extraction_content_error(out, DOC))

    def test_nested_introducers_are_all_kept(self):
        out = w.inherit_list_introducers(item(10), DOC)
        self.assertEqual([c['line'] for c in out['inherited_context']], [8, 9])
        self.assertEqual(out['source_ranges'], [[8, 8], [9, 9], [10, 10]])

    def test_partial_quote_keeps_content_and_records_context_only(self):
        out = w.inherit_list_introducers(item(6, '주문을 삭제'), DOC)
        self.assertEqual(out['source_ranges'], [[6, 6]]); self.assertEqual(out['content'], '주문을 삭제')
        self.assertEqual(out['inherited_context'], [{'line': 5, 'quote': '- Must not:'}])

    def test_top_level_and_paragraph_units_are_unchanged(self):
        for unit in (item(3), item(12)):
            self.assertIs(w.inherit_list_introducers(unit, DOC), unit)

    def test_partial_or_invalid_units_are_unchanged(self):
        for flag in ('judge_partial', 'range_invalid'):
            unit = dict(item(6), **{flag: 'x'})
            self.assertIs(w.inherit_list_introducers(unit, DOC), unit)


if __name__ == '__main__':
    unittest.main()
