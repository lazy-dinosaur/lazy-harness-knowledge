"""Stage1→2→3 contracts against actual product functions, never paid services."""
import test_offline  # Install existing no network/process/DB guard before app imports.
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import place_write as w
from confirmed_area_catalog import CONFIRMED_AREAS
from safety import Stop, digest, load, save

SOURCE = ('# Map-First Retrieval Vocabulary\n\n'
          'Context retrieval recovers host/project context before answering or editing.\n')
FINAL_CAPTURED_PROBABILITIES = {
    '0': 0., '1': .01, '2': 0., '3': 0., '4': 0., '5': 0.,
    '6': .02, '7': .12, '8': .26, '9': .03, 'none': .56,
}


class OfflineStagePorts:
    """Only role results are simulated; extraction/placement code stays real."""
    def __init__(self, text=SOURCE, *, none=.56, extract_answer=None):
        self.text = text
        self.none = none
        self.extract_answer = extract_answer
        self.reextract_answer = {'target_expression': 'STILL NOT PRESENT', 'reason': 'fixture'}
        self.events = []

    def forbidden(self, *args, **kwargs):
        raise AssertionError('stage4+/legacy/provider side effect forbidden')

    def worker(self, role, packet):
        self.events.append(('worker', role, copy.deepcopy(packet)))
        if role == 'extract':
            if isinstance(self.extract_answer, BaseException):
                raise self.extract_answer
            if callable(self.extract_answer):
                return self.extract_answer(packet)
            if self.extract_answer is not None:
                return copy.deepcopy(self.extract_answer)
            return {'items': [{'content': self.text.splitlines()[2],
                               'source_ranges': [[3, 3]],
                               'target_expression': 'host/project context'}]}
        if role == 'reextract':
            # Task378: one extraction-stage target re-extract for an ungrounded owner expression.
            return copy.deepcopy(self.reextract_answer)
        if role == 'domain-name':
            return {'original_expression': 'host/project context',
                    'source_concept': 'host/project context', 'name': '프로젝트',
                    'description': '호스트와 프로젝트 맥락을 복원하는 업무 영역'}
        return self.forbidden(role)

    def encode(self, values):
        self.events.append(('encode', tuple(values)))
        return [[1., 0.] for _ in values]

    def choose(self, kind, packet, options):
        self.events.append(('choose', kind, copy.deepcopy(packet), copy.deepcopy(options)))
        if kind != 'domain':
            return self.forbidden(kind)
        if set(options) == set(FINAL_CAPTURED_PROBABILITIES) and self.none == .56:
            return copy.deepcopy(FINAL_CAPTURED_PROBABILITIES)
        values = {key: 0. for key in options}
        values['none'] = self.none
        candidates = [key for key in options if key != 'none']
        if candidates:
            values[candidates[0]] = 1. - self.none
        return values

    def ask(self, packet, labels, question):
        self.events.append(('ask', copy.deepcopy(packet), tuple(labels), question))
        if question == w.DOMAIN_QUESTION:
            return [.9]
        if question in (w.DOMAIN_SAME_QUESTION, w.DOMAIN_COVERED_QUESTION):
            return [.1]
        if question == w.DOMAIN_MEMBERSHIP_QUESTION:  # Task377 stage-0 repair: ambiguous-top membership question added later
            return [.1]
        if question == w.DOMAIN_OVERLAP_QUESTION:  # Task382
            return [.1]
        return self.forbidden(question)

    def ports(self):
        return w.Ports(self.forbidden, self.encode, self.choose, self.ask,
                       catalog=lambda: [], writer=self.forbidden, worker=self.worker,
                       ground=self.forbidden)

    def domain_events(self):
        return [event for event in self.events
                if event[0] == 'choose' or (event[0] == 'worker' and event[1] == 'domain-name')]


class StepFixture:
    def __init__(self, case, text=SOURCE, **port_options):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        case.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.text = text
        self.path = self.home / 'v1' / 'input.md'
        self.path.parent.mkdir()
        self.path.write_text(text, encoding='utf-8')
        self.record = {'record_id': 'STAGE-OFFLINE', 'path': 'input.md',
                       'sha256': hashlib.sha256(text.encode()).hexdigest()}
        self.plan = {'write_path': w.VERSION, 'host': 'offline-stage-host',
                     'records': [self.record], 'candidate_policy': {'top_k': 5},
                     'area_catalog': True, 'embedding_precompute': True,
                     'fresh_database': True}
        save(self.home / 'state' / 'plan.json', self.plan)
        save(self.home / 'state' / 'area-catalog.json', {
            'confirmed_by_user': True, 'plan_sha': digest(self.plan),
            'areas': [{'name': name, 'description': description}
                      for name, description in CONFIRMED_AREAS]})
        self.fake = OfflineStagePorts(text, **port_options)
        self.paid = SimpleNamespace(scope='')

    def dispatch(self):
        return w.run('judge', self.plan, self.home, self.paid, self.fake.ports())

    def complete(self):
        return w.assert_completed(self.plan, self.home)


class InputExtractDomainStages(unittest.TestCase):
    def test_01_input_sha_failure_stops_before_extraction(self):
        f = StepFixture(self)
        f.path.write_text(f.text + 'Changed source.\n', encoding='utf-8')
        with self.assertRaisesRegex(Stop, 'source drift'):
            f.dispatch()
        self.assertFalse(any(e[0] == 'worker' for e in f.fake.events))

    def test_02_input_plan_drift_stops_before_roles(self):
        f = StepFixture(self)
        save(f.home / 'state' / 'plan.json', {**f.plan, 'host': 'different-host'})
        with self.assertRaisesRegex(Stop, 'plan/state drift'):
            f.dispatch()
        self.assertEqual(f.fake.events, [])

    def test_03_unconfirmed_catalog_stops_before_extraction(self):
        f = StepFixture(self)
        value = load(f.home / 'state' / 'area-catalog.json')
        value['confirmed_by_user'] = False
        save(f.home / 'state' / 'area-catalog.json', value)
        with self.assertRaisesRegex(Stop, 'confirmed_by_user'):
            f.dispatch()
        self.assertEqual(f.fake.events, [])

    def test_04_extract_packet_has_only_role_local_input(self):
        f = StepFixture(self)
        source = w.source(f.record, f.home)
        items = w.judge_input(f.fake.ports(), 'input', source,
                              origin='v1-document', migration=True)
        packet = f.fake.events[0][2]
        self.assertEqual(set(packet), {'source', 'source_start', 'chunk_range',
                                      'context', 'origin', 'migration'})
        # Task377 stage-0 repair: judge_input sends each chunk as its lines plus one terminating newline.
        self.assertEqual(packet['source'], '\n'.join(f.text.splitlines()) + '\n')
        self.assertEqual(packet['chunk_range'], [1, 3])
        self.assertEqual(packet['context']['document_title'], 'Map-First Retrieval Vocabulary')
        self.assertTrue(packet['migration'])
        self.assertEqual(items[0]['source_ranges'], [[3, 3]])
        self.assertEqual(items[0]['content'], source.splitlines()[2])
        self.assertEqual(items[0]['target_expression'], 'host/project context')

    def test_05_real_input_extract_output_feeds_real_domain_consumer(self):
        f = StepFixture(self)
        source = w.source(f.record, f.home)
        item = w.judge_input(f.fake.ports(), 'seam', source, origin='document')[0]
        before = copy.deepcopy(item)
        catalog = [{'name': '하네스', 'description': '프로젝트 맥락 복원 업무 영역',
                    'aliases': ['host/project context']}]
        chosen = w.place_identity('domain', item, None, catalog, f.fake.ports(), 5,
                                  document=source)
        self.assertEqual(chosen['chosen']['name'], '하네스')
        self.assertFalse(chosen['new'])
        self.assertEqual(item, before)
        self.assertEqual([e[1] for e in f.fake.events if e[0] == 'worker'], ['extract'])
        self.assertFalse(f.fake.domain_events())

    def test_06_chunk_global_ranges_survive_into_distinct_units(self):
        text = '\n'.join(['# Context'] + ['filler context'] * 38 +
                         ['shared target detail', 'shared target detail'])
        def answer(packet):
            lo, hi = packet['chunk_range']
            return {'items': [{'content': 'shared target detail', 'source_ranges': [[n, n]],
                               'target_expression': 'target'} for n in (40, 41) if lo <= n <= hi]}
        f = StepFixture(self, text, extract_answer=answer)
        items = w.judge_input(f.fake.ports(), 'chunks', w.source(f.record, f.home),
                              origin='document', migration=True)
        self.assertEqual([item['source_ranges'] for item in items], [[[40, 40]], [[41, 41]]])
        self.assertEqual([e[2]['source_start'] for e in f.fake.events if e[0] == 'worker'], [1, 41])
        self.assertEqual(len(items), 2)  # Same words, different provenance are not one unit.

    def test_07_extractor_cannot_add_domain_authority_fields(self):
        f = StepFixture(self, extract_answer={'items': [{
            'content': SOURCE.splitlines()[2], 'source_ranges': [[3, 3]],
            'target_expression': 'host/project context', 'domain': 'invented'}]})
        with self.assertRaisesRegex(Stop, 'no names/domains/facts'):
            f.dispatch()
        self.assertFalse(f.fake.domain_events())

    def test_08_bad_ranges_become_hold_before_domain(self):
        f = StepFixture(self, extract_answer={'items': [{
            'content': SOURCE.splitlines()[2], 'source_ranges': [[99, 99]],
            'target_expression': 'host/project context'}]})
        f.dispatch()
        row = f.complete()['units'][0]
        self.assertEqual(row['held_reasons'][0]['code'], 'E_RANGE')
        self.assertFalse(f.fake.domain_events())

    def test_09_ungrounded_target_becomes_hold_before_domain(self):
        f = StepFixture(self, extract_answer={'items': [{
            'content': SOURCE.splitlines()[2], 'source_ranges': [[3, 3]],
            'target_expression': 'NOT PRESENT IN THE SOURCE'}]})
        f.dispatch()
        self.assertEqual(f.complete()['units'][0]['held_reasons'][0]['code'], 'E_TARGET')
        self.assertFalse(f.fake.domain_events())
        # Task378: exactly one target re-extract was asked, and its still-ungrounded answer kept the HOLD.
        self.assertEqual([e[1] for e in f.fake.events if e[0] == 'worker'].count('reextract'), 1)

    def test_09b_grounded_target_reextract_clears_the_flag(self):
        f = StepFixture(self, extract_answer={'items': [{
            'content': SOURCE.splitlines()[2], 'source_ranges': [[3, 3]],
            'target_expression': 'NOT PRESENT IN THE SOURCE'}]})
        f.fake.reextract_answer = {'target_expression': 'host/project context', 'reason': 'verbatim owner'}
        items = w.judge_input(f.fake.ports(), 'fixed', f.text, origin='document')
        self.assertNotIn('target_ungrounded', items[0])
        self.assertEqual(items[0]['target_expression'], 'host/project context')
        self.assertEqual(items[0]['target_reextract']['before'], 'NOT PRESENT IN THE SOURCE')

    def test_10_no_replay_stop_is_not_split_or_forwarded(self):
        f = StepFixture(self, extract_answer=Stop('paid/no-replay STOP fixture'))
        with self.assertRaisesRegex(Stop, 'no-replay'):
            w.judge_input(f.fake.ports(), 'stop', f.text, origin='document')
        self.assertEqual(len(f.fake.events), 1)
        self.assertFalse(f.fake.domain_events())

    def test_11_domain_creation_requires_explicit_none_threshold(self):
        f = StepFixture(self, none=.7)
        item = w.judge_input(f.fake.ports(), 'new', f.text, origin='document')[0]
        catalog = [{'name': n, 'description': d, 'aliases': []} for n, d in CONFIRMED_AREAS]
        chosen = w.place_identity('domain', item, None, catalog, f.fake.ports(), 5,
                                  document=f.text)
        self.assertTrue(chosen['new'])
        self.assertEqual(chosen['chosen']['name'], '프로젝트')
        self.assertNotIn('subject_id', chosen['chosen'])
        self.assertEqual([e[1] for e in f.fake.events if e[0] == 'worker'], ['extract', 'domain-name'])

    def test_12_captured_none_056_is_typed_hold_with_full_source_trace(self):
        f = StepFixture(self)
        item = w.judge_input(f.fake.ports(), 'held', f.text, origin='document')[0]
        catalog = [{'name': n, 'description': d, 'aliases': []} for n, d in CONFIRMED_AREAS]
        with self.assertRaisesRegex(w.PlacementHeld, 'explicit Jev none') as held:
            w.place_identity('domain', item, None, catalog, f.fake.ports(), 5, document=f.text)
        trace = held.exception.trace['domain_creation_choice']
        self.assertEqual([c['domain'] for c in trace['candidates']], [row['name'] for row in catalog])
        self.assertEqual({c['key']: c['probability'] for c in trace['candidates']} | {'none': trace['none']}, FINAL_CAPTURED_PROBABILITIES)
        for event in f.fake.events:
            if event[0] == 'choose':
                self.assertEqual(event[2]['source'], f.text)
                self.assertEqual(event[2]['source_start_line'], 1)
        self.assertNotIn('chosen', held.exception.trace)

    def test_13_real_dispatcher_persists_hold_and_receipts_without_downstream_calls(self):
        f = StepFixture(self)
        f.dispatch()
        complete = f.complete()
        row = complete['units'][0]
        self.assertEqual(row['held_reasons'][0]['where'], 'domain')
        self.assertIn('explicit Jev none', row['held_reasons'][0]['detail'])
        self.assertEqual(row['domain']['domain_creation_choice']['none'], .56)
        generated = load(f.home / 'state' / 'generated' / 'STAGE-OFFLINE.json')
        self.assertEqual(generated['status'], 'partial')
        self.assertEqual(generated['facts'], [])
        self.assertEqual(generated['held_units'][0]['unit_id'], row['unit_id'])
        before = copy.deepcopy(f.fake.events)
        for phase in ('place', 'write', 'ground'):
            w.run(phase, f.plan, f.home, f.paid, f.fake.ports())
        self.assertEqual(f.fake.events, before)  # Receipt reads, not another execution.
        self.assertEqual([e[1] for e in f.fake.events if e[0] == 'worker'], ['extract', 'domain-name'])

    def test_14_malformed_choice_stops_before_naming(self):
        f = StepFixture(self)
        ports = f.fake.ports()
        from dataclasses import replace
        ports = replace(ports, choose=lambda kind, packet, options: {'none': .56})
        item = w.judge_input(f.fake.ports(), 'bad-choice', f.text, origin='document')[0]
        catalog = [{'name': n, 'description': d, 'aliases': []} for n, d in CONFIRMED_AREAS]
        with self.assertRaisesRegex(Stop, 'typed probabilities'):
            w.place_identity('domain', item, None, catalog, ports, 5, document=f.text)
        self.assertNotIn('domain-name', [e[1] for e in f.fake.events if e[0] == 'worker'])

    def test_15_invented_extraction_content_must_not_reach_domain(self):
        # Requirement: extraction content is verbatim source, not a new model claim.
        # This is intentionally a required regression, NOT an expectedFailure waiver.
        f = StepFixture(self, extract_answer={'items': [{
            'content': '원문에 없는 결제 승인 사실을 모델이 만들어 냈다.',
            'source_ranges': [[3, 3]], 'target_expression': 'host/project context'}]})
        f.dispatch()
        self.assertEqual(f.fake.domain_events(), [],
                         'unbacked extraction content reached real domain selection before grounding')


    def test_16_partial_quote_inside_declared_range_is_preserved(self):
        quote = 'recovers host/project context'
        f = StepFixture(self, extract_answer={'items': [{
            'content': quote, 'source_ranges': [[3, 3]],
            'target_expression': 'host/project context'}]})
        item = w.judge_input(f.fake.ports(), 'part', f.text, origin='document')[0]
        self.assertEqual(item['content'], quote)
        self.assertNotIn('content_ungrounded', item)
        chosen = w.place_identity('domain', item, None,
            [{'name': '하네스', 'description': '프로젝트 맥락 복원 영역',
              'aliases': ['host/project context']}], f.fake.ports(), 5, document=f.text)
        self.assertEqual(chosen['chosen']['name'], '하네스')

    def test_17_content_elsewhere_in_document_is_not_in_declared_range(self):
        f = StepFixture(self, SOURCE + 'Payment was approved.\n', extract_answer={'items': [{
            'content': 'Payment was approved.', 'source_ranges': [[3, 3]],
            'target_expression': 'host/project context'}]})
        f.dispatch()
        held = f.complete()['units'][0]['held_reasons'][0]
        self.assertEqual(held['code'], 'E_SOURCE_CONTENT')
        self.assertEqual(held['where'], 'judge')
        self.assertFalse(f.fake.domain_events())

    def test_18_domain_consumer_rechecks_cached_unflagged_extraction(self):
        f = StepFixture(self)
        unit = {'content': 'Invented claim.', 'source_ranges': [[3, 3]],
                'target_expression': 'host/project context'}
        before = copy.deepcopy(unit)
        with self.assertRaisesRegex(w.PlacementHeld, 'E_SOURCE_CONTENT') as held:
            w.place_identity('domain', unit, None, [], f.fake.ports(), 5, document=f.text)
        self.assertFalse(held.exception.trace['source_content_check']['passed'])
        self.assertEqual(unit, before)
        self.assertEqual(f.fake.events, [])

    def test_19_old_completion_binding_is_refused_without_role_replay(self):
        f = StepFixture(self)
        f.dispatch()
        path = f.home / 'state' / 'place-write-complete.json'
        complete = load(path)
        legacy = digest([w.LUNA_BINDING, w.JEV_BINDING])
        self.assertNotEqual(legacy, w.ACTIVE_BINDING)
        complete['active_binding'] = legacy
        save(path, complete)
        f.fake.events.clear()
        with self.assertRaisesRegex(Stop, 'completion drift'):
            w.run('place', f.plan, f.home, f.paid, f.fake.ports())
        self.assertEqual(f.fake.events, [])

    def test_20_current_cached_judge_reply_cannot_bypass_consumer_guard(self):
        f = StepFixture(self)
        f.dispatch()
        path = f.home / 'state' / 'place-write' / 'judge' / 'STAGE-OFFLINE.json'
        receipt = load(path)
        receipt['result'][0]['content'] = 'Invented cached claim.'
        receipt['result'][0].pop('content_ungrounded', None)
        receipt['result_sha'] = digest(receipt['result'])  # Synthetic valid-hash cache only.
        save(path, receipt)
        f.fake.events.clear()
        f.dispatch()
        held = f.complete()['units'][0]['held_reasons'][0]
        self.assertEqual(held['code'], 'E_SOURCE_CONTENT')
        self.assertEqual(held['where'], 'judge')
        self.assertEqual(f.fake.events, [])  # Neither extraction replay nor paid domain work.

    def test_21_multiple_declared_ranges_allow_raw_quote_not_rewriting(self):
        text = 'first quoted line\nnot selected\nsecond quoted line'
        answer = {'items': [{'content': 'first quoted line\nsecond quoted line',
                            'source_ranges': [[1, 1], [3, 3]],
                            'target_expression': 'quoted'}]}
        checked = w.judge_items(answer, text)[0]
        self.assertNotIn('content_ungrounded', checked)
        self.assertEqual(checked['content'], answer['items'][0]['content'])
        self.assertEqual(checked['source_ranges'], [[1, 1], [3, 3]])
        answer['items'][0]['content'] = 'second quoted line\nfirst quoted line'
        self.assertIn('content_ungrounded', w.judge_items(answer, text)[0])

    def test_22_translation_or_added_claim_is_held_without_changing_content(self):
        for content in ('맥락을 복원한다.', 'Context retrieval approved a payment.'):
            with self.subTest(content=content):
                original = {'items': [{'content': content, 'source_ranges': [[3, 3]],
                                       'target_expression': 'host/project context'}]}
                before = copy.deepcopy(original)
                checked = w.judge_items(original, SOURCE)[0]
                self.assertIn('content_ungrounded', checked)
                self.assertEqual(checked['content'], content)
                self.assertEqual(original, before)


if __name__ == '__main__':
    unittest.main()
