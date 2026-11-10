"""Behaviour-level conformance of the Pi host's writer and worker against the core kit (fake transport, no Pi)."""
import offline_guard  # noqa: F401
import json, unittest

import port_conformance as kit
import active_worker as c
import active_worker_transport as t
import writer_transport as wt
import test_active_worker_offline as samples

INSPECT = lambda: {'ok': True, 'inputBudget': 1000000}


def shim(factory):
    def make_port(paid, respond):
        def send(request):
            kind, value = respond(request)
            return {'ok': True, 'text': value, 'cost': .01} if kind == 'ok' else {'ok': False, 'code': value}
        return factory(paid, inspect=INSPECT, send=send)
    return make_port


class PiRolePortBehaviour(unittest.TestCase):
    def test_worker_all_roles(self):
        cases = [(role, samples.evidence(role), json.dumps(samples.reply(role), ensure_ascii=False)) for role in c.ROLES]
        results = kit.run(shim(t.make_worker), cases)
        self.assertEqual(results, [samples.reply(role) for role in c.ROLES])

    def test_writer_write_and_rewrite(self):
        unit = samples.evidence('subject-name')['unit']
        packet = {'source': samples.RAW, 'unit': unit,
                  'chosen_domain': {'name': '물류', 'description': '물류 업무', 'aliases': []},
                  'chosen_subject': {'name': '배송함', 'description': '보관 대상', 'aliases': [], 'subject_id': 'PRIVATE_UUID'}}
        original = {'fact': '배송함은 4도를 유지한다.', 'keywords': ['온도'], 'source_ranges': [[1, 1]], 'reason': '원문 보존'}
        rewrite = dict(packet, original_fields_not_evidence=original, errors=[{'code': 'E_SENTENCE_UNGROUNDED', 'detail': '같은 대상 재작성'}])
        cases = [('write', packet, json.dumps(original, ensure_ascii=False)),
                 ('rewrite', rewrite, json.dumps({'fact': '배송함은 4도를 유지한다.'}, ensure_ascii=False))]
        results = kit.run(shim(wt.make_writer), cases)
        self.assertEqual(results[0]['fact'], original['fact'])


if __name__ == '__main__':
    unittest.main()
