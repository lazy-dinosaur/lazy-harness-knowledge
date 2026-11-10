"""Experiment-runner cases split out of packages/core/tests/test_active_worker_offline.py.py (issue #6 step 7)."""
import offline_guard  # noqa: F401
from offline_guard import forbidden
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import pipeline as p
import generation
import place_write as w
import active_worker as c
import active_worker_transport as t
import active_judgements as j
import source_grounding as sg
import digest_driver as dd
import store_pg
from safety import Ledger, Stop, load, save, digest
from model_output import ModelOutputInvalid
from writer_worker import build_request as writer_request
import test_place_write_offline as previous
import test_active_worker_offline as _core
import unittest as _ut
globals().update({k: v for k, v in vars(_core).items() if not k.startswith('__') and not (isinstance(v, type) and issubclass(v, _ut.TestCase))})


class ActiveContractsEval(unittest.TestCase):
    # setUp/tearDown/helpers come from the core case; only the experiment-runner tests live here
    locals().update({k: v for k, v in vars(_core.ActiveContracts).items() if not k.startswith("test") and not k.startswith("__")})

    def test_production_registration_and_no_legacy_fallback(self):
        worker=Mock(return_value=reply('extract')); writer=Mock()
        ports=w.production(Mock(),CFG,worker_factory=lambda paid:worker,writer_factory=lambda paid:writer)
        self.assertIs(ports.worker,worker); self.assertIs(ports.writer,writer)
        self.assertIsNotNone(ports.ground)
        with self.assertRaises(c.WorkerFailure): ports.model('judge-random',{},'arbitrary task')
        self.assertIn('pilot-area-10-isolated',p.SCOPES)

    def test_maintenance_explicit_roles_and_injected_generator_compatibility(self):
        seen=[]
        def generator(snapshot,task):
            seen.append(task)
            return {'description':'기존 보존'} if task==c.PROMPTS['describe'] else reply('merge-luna')
        _,maintenance=p.adapters(CFG,Mock(),generator=generator)
        self.assertEqual(maintenance['describe'](evidence('describe')),'기존 보존')
        self.assertEqual(maintenance['merge_luna'](evidence('merge-luna'))['relation'],'unknown')
        self.assertEqual(seen,[c.PROMPTS['describe'],c.PROMPTS['merge-luna']])
        with tempfile.TemporaryDirectory() as tmp,patch.object(p,'HERE',Path(tmp)),patch.object(generation,'HERE',Path(tmp)):
            def broken(role,snapshot): raise ModelOutputInvalid('malformed paid response')
            _,maintenance=p.adapters(CFG,Mock(scope='test'),worker_factory=lambda paid:broken)
            with self.assertRaises(ModelOutputInvalid): maintenance['describe'](evidence('describe'))
            self.assertEqual(next(iter(load(Path(tmp)/'state/maintenance-failures.json').values()))['status'],'description_failed')

    def test_actual_description_failure_preserves_metadata_and_vectors(self):
        import subject_dict as sd, domain_router, embedding_guard
        old_evidence=[('fragment-ref',RAW,{'evidence_quote':RAW})]
        queries=[]
        class Cursor:
            def execute(self,sql,args=None):
                queries.append(sql)
                if not sql.lstrip().startswith('select '): raise AssertionError('failed description must not write')
            def fetchall(self):
                return [('subject-ref','배송함','기존 설명','entity')] if 'select subject_id' in queries[-1] else copy.deepcopy(old_evidence)
        with tempfile.TemporaryDirectory() as tmp,patch.object(p,'HERE',Path(tmp)),patch.object(generation,'HERE',Path(tmp)):
            def failed(role,snapshot): raise ModelOutputInvalid('journaled malformed output')
            _,maintenance=p.adapters(CFG,Mock(scope='test'),worker_factory=lambda paid:failed)
            with patch.object(domain_router,'lock_partition'),patch.object(embedding_guard,'assert_host'):
                result=sd.maintain(Cursor(),'synthetic','물류',maintenance['describe'],test_offline.forbidden)
        self.assertEqual([r['status'] for r in result],['description_failed','description_failed'])
        self.assertEqual(old_evidence,[('fragment-ref',RAW,{'evidence_quote':RAW})])
        self.assertEqual(len(queries),2)


class ActualControllerEval(unittest.TestCase):
    # setUp/tearDown/helpers come from the core case; only the experiment-runner tests live here
    locals().update({k: v for k, v in vars(_core.ActualController).items() if not k.startswith("test") and not k.startswith("__")})

    def test_production_roles_different_targets_same_content_preserved(self):
        raw='# 채팅방\n내용을 저장한다.\n# 일정표\n내용을 저장한다.'
        captured=[]
        class Fake(previous.Fake):
            def choose(self,kind,packet,options):
                if kind=='fidelity':return {'supported':1.0,'mismatch':0.0,'uncertain':0.0}  # Task377 stage-0 repair: source_modality v3 fidelity
                if kind=='modality':
                    # Deterministic fixture describes storage; not an actual model accuracy claim.
                    return {k:float(k=='description') for k in options}
                wanted='fact' if kind=='kind' else next((r['key'] for r in packet.get('candidates',[]) if r['name']=='업무'),'none') if kind=='domain' else 'none'
                return {k:.99 if k==wanted else .001 for k in options}
        fake=Fake(raw)
        def worker(role,packet):
            request=c.build_request(role,packet,1000000); captured.append((role,request))
            if role=='extract':
                start,end=packet['chunk_range']; target=packet['context']['heading_path'][-1]
                return {'items':[{'content':'내용을 저장한다.','source_ranges':[[end,end]],'target_expression':target}]} if end>start else {'items':[]}
            if role=='subject-name':
                target=packet['unit']['target_expression']
                return {'original_expression':target,'source_concept':target,'name':target,'description':target+' 저장 대상'}
            raise AssertionError('unexpected role '+role)
        writer_packets=[]
        def writer(stage,packet):
            writer_packets.append(copy.deepcopy(packet)); self.assertEqual(stage,'write',packet.get('errors'))
            return {'fact':packet['chosen_subject']['name']+'은 내용을 저장한다.','keywords':['저장'],'reason':'원문 보존','source_ranges':packet['unit']['source_ranges']}
        ports=w.production(Mock(),CFG,worker_factory=lambda paid:worker,writer_factory=lambda paid:writer)
        from dataclasses import replace
        ports=replace(ports,encode=fake.encode,choose=fake.choose,ask=fake.ask,ground=fake.ask)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            home=Path(tmp); path=home/'v1/record.md'; path.parent.mkdir(); path.write_text(raw)
            record={'record_id':'synthetic-active','path':'record.md','sha256':hashlib.sha256(raw.encode()).hexdigest()}
            plan={'write_path':2,'host':'synthetic-host','records':[record],'candidate_policy':{'top_k':5},'area_catalog':True,'embedding_precompute':True,'fresh_database':True}
            save(home/'state/plan.json',plan)
            save(home/'state/area-catalog.json',{'confirmed_by_user':True,'plan_sha':digest(plan),'areas':[{'name':'업무','description':'저장 업무 영역'}]})
            with patch.object(p,'HERE',home),patch.object(generation,'HERE',home):
                w.run('judge',plan,home,Mock(),ports)
                complete=w.assert_completed(plan,home)
                for phase in ('place','write','ground'): w.run(phase,plan,home,Mock(),ports)
            self.assertEqual([(q['unit']['target_expression'],q['unit']['source_ranges']) for q in writer_packets],[('채팅방',[[2,2]]),('일정표',[[4,4]])])
            self.assertEqual(len({r['unit_id'] for r in complete['units']}),2)
            self.assertTrue(all(not row['held_reasons'] for row in complete['units']))
            self.assertEqual(complete['active_binding'],w.ACTIVE_BINDING)
            stale=load(home/'state/place-write-complete.json'); stale['active_binding']='forged'; save(home/'state/place-write-complete.json',stale)
            with self.assertRaises(Stop): w.assert_completed(plan,home)


if __name__ == '__main__':
    unittest.main()
