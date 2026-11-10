"""Confirmed placement decisions: typed fakes only, guard before application imports."""
import test_offline
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
import place_write as w
import place_write_absorb as a
import pipeline as p
import local_db
import subject_contract as sc
import subject_dict as sd
import store_pg as pg
from ddd_criterion import DDD2, CALIBRATED_SHA256, criterion_sha, DDD_FOR_AUTHOR
from safety import Stop, digest, load, save, RUNNER
import test_place_write_offline as previous


class PlacementDecisions(unittest.TestCase):
    def fixture(self):
        case = previous.UnitFirst(); case.setUp(); self.addCleanup(case.doCleanups)
        return case

    def subject_ports(self, *, ddd=(.99,), same=.01, calls=None):
        events = calls if calls is not None else []
        scores = iter(ddd)
        def model(name: str, packet: dict, task: str) -> dict:
            events.append(('model',name,copy.deepcopy(packet),task))
            if name.startswith('re-extract-'): return {'target_expression':'채팅방','reason':'정책을 가진 엔티티'}
            target = packet['unit']['target_expression']
            return {'original_expression':target,'source_concept':target,'name':target,'description':target+'의 정체성\n원문 상태를 추적하는 대상'}
        def encode(texts: list[str]) -> list[list[float]]:
            events.append(('encode',list(texts)))
            return [[0.,1.] for _ in texts]
        def choose(kind: str, packet: dict, options: dict[str,str]) -> dict[str,float]:
            events.append(('choose',kind,copy.deepcopy(packet)))
            return {k:.99 if k=='none' else .001 for k in options}
        def ask(state: dict, texts: list[str], question: str) -> list[float]:
            events.append(('ask',question,copy.deepcopy(state)))
            return [next(scores)] if question==DDD2 else [.99] if question==w.DOMAIN_QUESTION else [same]
        return w.Ports(model,encode,choose,ask)

    def test_exact_calibrated_criterion_hash(self):
        # The 56-candidate gold measurement (54/56) was made on host-derived names and stays in lazy-harness;
        # this repository only pins the calibrated criterion text and its drift guard.
        self.assertEqual(criterion_sha(),CALIBRATED_SHA256)
        with patch('ddd_criterion.DDD2',DDD2+' drift'):
            with self.assertRaisesRegex(ValueError,'drift'): w.assert_calibrated()

    def test_judge_owner_policy_action_part_and_protected_entity(self):
        text='채팅방 가시성 정책은 채팅방에 적용된다.\nRunner 자원 조정은 Runner가 수행한다.\n모달의 닫기 버튼은 모달을 닫는다.\n일정표 회귀 보호는 일정표의 시간을 검사한다.'
        targets=['채팅방','Runner','모달','일정표']
        def model(name: str, packet: dict, task: str) -> dict:
            self.assertIn(DDD_FOR_AUTHOR,task); self.assertNotIn('{i}',task)
            for marker in ('정책→','동작/과정→','부분→','테스트/기록→'): self.assertIn(marker,task)
            return {'items':[{'content':line,'source_ranges':[[i,i]],'target_expression':target} for i,(line,target) in enumerate(zip(text.splitlines(),targets),1)]}
        ports=w.Ports(model,Mock(),Mock(),Mock())
        result=w.judge_input(ports,'owner',text,origin='document')
        self.assertEqual([r['target_expression'] for r in result],targets)
        self.assertTrue(all(set(r)=={'content','source_ranges','target_expression'} for r in result))
        flagged=w.judge_items({'items':[{'content':text,'source_ranges':[[1,1]],'target_expression':'AI상담'}]},text); self.assertTrue(flagged[0].get('target_ungrounded'))
        self.assertEqual(w.model_view({'a':[{'vector':[1.0]*9,'name':'x'}],'vector':[2.0]}),{'a':[{'name':'x'}]})
        self.assertEqual(w.normalize_ranges([1,1]),[[1,1]]); self.assertEqual(w.normalize_ranges([[2]]),[[2,2]])
        bad=w.judge_items({'items':[{'content':text,'source_ranges':'x','target_expression':text.split()[0]}]},text); self.assertTrue(bad[0].get('range_invalid'))
        self.assertTrue(w.grounded_in('DEPARTMENT custom/tracked room','`DEPARTMENT` custom/tracked room')); self.assertFalse(w.grounded_in('GROUP DIRECT','`GROUP` / `DIRECT`'))

    def test_ddd_reextract_once_then_pass_uses_owner_and_no_type_parent(self):
        events=[]; ports=self.subject_ports(ddd=(.1,.91),calls=events)
        unit={'host_id':'fake-host','unit_id':'30000000-0000-4000-8000-000000000003','content':'채팅방 가시성 정책은 채팅방에 적용된다.','target_expression':'채팅방 가시성 정책'}
        result=w.place_identity('subject',unit,{'name':'채팅','description':'대화 업무'},[],ports,5)
        self.assertEqual(result['chosen']['name'],'채팅방')
        self.assertEqual([x['score'] for x in result['ddd_checks']],[.1,.91])
        self.assertEqual(result['re_extract']['after'],'채팅방')
        self.assertIn('score=0.1',result['re_extract']['failure_reason'])
        self.assertEqual(sum(e[0]=='model' and e[1].startswith('re-extract-') for e in events),1)
        self.assertNotIn('subject_type',result['chosen']); self.assertNotIn('parent_subject',result['chosen'])
        self.assertEqual(len(result['chosen']['description'].splitlines()),2)
        self.assertTrue(result['chosen']['vector'])

    def test_ddd_still_fail_is_held_with_two_scores_and_one_retry(self):
        events=[]; unit={'host_id':'fake-host','unit_id':'30000000-0000-4000-8000-000000000003','content':'채팅방 가시성 정책은 채팅방에 적용된다.','target_expression':'채팅방 가시성 정책'}
        with self.assertRaises(w.PlacementHeld) as caught:
            w.place_identity('subject',unit,{'name':'채팅','description':'대화'},[],self.subject_ports(ddd=(.1,.2),calls=events),5)
        self.assertIn('after one',str(caught.exception))
        self.assertEqual(len(caught.exception.trace['ddd_checks']),2)
        self.assertEqual(sum(e[0]=='model' for e in events),1)

    def test_target_search_then_full_content_once_and_full_content_in_choice(self):
        unit={'host_id':'fake-host','unit_id':'30000000-0000-4000-8000-000000000003','content':'주문관리파트의 일정표 시간은 설정에서 정해진다.','target_expression':'일정표'}
        queries=[]; choices=[]
        def encode(texts: list[str]) -> list[list[float]]:
            queries.append(texts); return [[1.,0.] for _ in texts]
        def choose(kind: str, packet: dict, options: dict[str,str]) -> dict[str,float]:
            choices.append(packet)
            self.assertEqual(packet['unit']['content'],unit['content'])
            chosen='none' if len(choices)==1 else 'c0'
            return {k:.99 if k==chosen else .001 for k in options}
        model=Mock(return_value={'original_expression':'주문관리파트'})
        ports=w.Ports(model,encode,choose,Mock(side_effect=AssertionError('no new identity check')))
        result=w.place_identity('domain',unit,None,[{'name':'예약','description':'예약을 다루는 영역'}],ports,5)
        self.assertEqual([x[0] for x in queries[:2]],['일정표',unit['content']])
        self.assertFalse(result['new']); self.assertEqual(result['chosen']['name'],'예약')
        self.assertEqual(result['chosen']['aliases'],[]);model.assert_not_called()
        self.assertEqual([r['query_kind'] for r in result['searches']],['target-expression','full-content'])

    def test_create_only_after_pair_checks_or_use_existing_plus_alias(self):
        unit={'host_id':'fake-host','unit_id':'30000000-0000-4000-8000-000000000003','content':'일정표 시간은 포장단위에서 정해진다.','target_expression':'일정표'}
        catalog=[{'subject_id':'10000000-0000-4000-8000-000000000001','name':'예약 시트','description':'기존 시간표 대상','aliases':[]}]
        for same,expected_new in ((.92,False),(.05,True)):
            with self.subTest(same=same):
                events=[]; result=w.place_identity('subject',unit,{'name':'예약','description':'예약 영역'},catalog,self.subject_ports(same=same,calls=events),5)
                self.assertEqual(result['new'],expected_new)
                self.assertEqual(len(result['same_meaning_checks']),1)
                last_check=max(i for i,e in enumerate(events) if e[0]=='ask' and e[1]==w.SAME_QUESTION)
                if expected_new:
                    self.assertEqual(events[-1][0],'encode'); self.assertLess(last_check,len(events)-1)
                else:
                    self.assertEqual(result['chosen']['name'],'예약 시트'); self.assertIn('일정표',result['chosen']['aliases'])
                    self.assertIsNone(result['description_created'])

    def test_domain_pair_check_is_required_before_creation(self):
        events=[]; ports=self.subject_ports(same=.91,calls=events)
        unit={'host_id':'fake-host','unit_id':'30000000-0000-4000-8000-000000000003','content':'주문관리파트는 예약을 처리한다.','target_expression':'주문관리파트'}
        result=w.place_identity('domain',unit,None,[{'name':'예약','description':'예약 영역'}],ports,5)
        self.assertFalse(result['new']); self.assertEqual(result['chosen']['name'],'예약')
        self.assertIn('주문관리파트',result['chosen']['aliases'])
        self.assertEqual(len(result['same_meaning_checks']),1)

    def test_none_threshold_and_no_cosine_floor(self):
        unit={'host_id':'fake-host','unit_id':'30000000-0000-4000-8000-000000000003','content':'일정표 시간','target_expression':'일정표'}
        ports=w.Ports(Mock(return_value={'original_expression':'일정표'}),lambda xs:[[0.,0.]]*len(xs),
                      lambda k,p,o:{n:.69 if n=='none' else .31 for n in o},Mock(return_value=[.5]))
        result=w.place_identity('domain',unit,None,[{'name':'예약','description':'예약 영역'}],ports,5)
        self.assertFalse(result['new'])
        self.assertEqual(result['ambiguous_domain_membership']['score'],.5)
        self.assertTrue(result['ambiguous_domain_membership']['member'])
        self.assertNotIn('ambiguous_same_meaning',result)
        self.assertNotIn('일정표',result['chosen']['aliases'])  # ambiguous placement is not alias proof

    def test_immediate_description_vector_visible_to_next_unit_and_serial_ground(self):
        case=self.fixture(); case.run_phases()
        rows=w.assert_completed(case.plan,case.home)['units']
        first=rows[0]['subject']['chosen']; next_candidates=rows[1]['subject']['candidates']
        self.assertEqual(next_candidates[0]['description'],first['description'])
        self.assertEqual(next_candidates[0]['vector'],first['vector'])
        self.assertTrue(rows[0]['subject']['description_created'])
        # Fake logs both ports to prove ground of unit 0 precedes placement of unit 1.
        timeline=[]; original_choose=case.fake.choose; original_ask=case.fake.ask
        case2=self.fixture()
        def choose(kind: str, packet: dict, options: dict[str,str]) -> dict[str,float]:
            timeline.append((kind,packet['unit']['index'])); return original_choose(kind,packet,options)
        def ask(state: dict, texts: list[str], question: str) -> list[float]:
            if question in (w.DDD2,w.SAME_QUESTION): return original_ask(state,texts,question)
            timeline.append(('ground',None)); return original_ask(state,texts,question)
        ports=w.Ports(case2.fake.model,case2.fake.encode,choose,ask)
        w.run('judge',case2.plan,case2.home,case2.paid,ports)
        self.assertLess(timeline.index(('ground',None)),timeline.index(('domain',1)))

    def test_cross_document_reuse_and_summary(self):
        case=self.fixture()
        second=copy.deepcopy(case.r); second['record_id']='R06'
        case.plan['records'].append(second)
        save(case.home/'state/plan.json',case.plan)
        catalog=load(case.home/'state/area-catalog.json'); catalog['plan_sha']=digest(case.plan); save(case.home/'state/area-catalog.json',catalog)
        case.run_phases(); metrics=a.unit_report(case.plan,case.home)['metrics']
        self.assertEqual(metrics['domains_created'],1); self.assertEqual(metrics['subjects_count'],2)
        self.assertEqual(metrics['facts_per_subject']['mean'],3.)
        self.assertEqual(metrics['facts_per_subject']['max'],4)
        self.assertEqual(metrics['cross_document_subject_reuse'],2)
        self.assertEqual(metrics['singleton_subjects'],0)

    def test_kind_chosen_by_jev_after_write_not_luna(self):
        case=self.fixture(); original=case.fake.choose
        def choose(kind: str, packet: dict, options: dict[str,str]) -> dict[str,float]:
            if kind=='kind':
                self.assertNotIn('kind',packet['fields']); self.assertEqual(set(options),set(w.KINDS))
                return {k:.99 if k=='term' else .001 for k in options}
            return original(kind,packet,options)
        ports=w.Ports(case.fake.model,case.fake.encode,choose,case.fake.ask)
        w.run('judge',case.plan,case.home,case.paid,ports)
        rows=w.assert_completed(case.plan,case.home)['units']
        self.assertTrue(all(r['fact']['kind']=='term' for r in rows))
        self.assertIn('kind는 쓰거나 바꾸지 않는다',w.WRITE_TASK)

    def test_new_path_untyped_metadata_but_legacy_still_typed_and_no_parent(self):
        base={'subject':'일정표','domain':'예약','subject_description':'원문 시간표\n상태를 추적한다'}
        self.assertIn('E_SUBJECT_TYPE',{e['code'] for e in sc.metadata_errors(base)})
        with sc.unit_first_metadata():
            self.assertEqual(sc.metadata_errors(base),[])
            self.assertIn('E_SUBJECT_PARENT',{e['code'] for e in sc.metadata_errors({**base,'parent_subject_id':'parent'})})
        self.assertIn('E_SUBJECT_TYPE',{e['code'] for e in sc.metadata_errors(base)})
        case=self.fixture(); rows=case.run_phases()['units']
        self.assertTrue(all('subject_type' not in r['fact'] and 'parent_subject' not in r['fact'] for r in rows))

    def test_register_untyped_subject_does_not_invent_placeholder(self):
        cur=Mock(); cur.fetchone.return_value=('sid',)
        with sc.unit_first_metadata(),patch.object(sd,'_lock_name'),patch.object(sd,'lookup',return_value=None),patch.object(sd,'add_alias'):
            result=sd.register(cur,'h','일정표',allow_embed=False,domain='예약',description='원문 시간표')
        self.assertEqual(result,'sid')
        args=next(c.args[1] for c in cur.execute.call_args_list if 'insert into knowledge.subject(' in c.args[0])
        self.assertIsNone(args[3]); self.assertIsNone(args[5])

    def test_install_descriptions_aliases_and_vector_without_type_parent(self):
        case=self.fixture(); case.run_phases(); complete=w.assert_completed(case.plan,case.home)
        conn=MagicMock(); conn.__enter__.return_value=conn; cur=conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value=None;cur.fetchall.return_value=[]
        aliases={n:s['name'] for r in complete['units'] for s in [r['subject']['chosen']] for n in [s['name'],*s.get('aliases',[])]}
        with patch('area_catalog.install'),patch.object(pg,'connect',return_value=conn), \
             patch.object(sd,'register',side_effect=lambda *args,**kw:kw['subject_id']) as register,patch('identity_ref.add_spelling') as alias, \
             patch('identity_ref.resolve',side_effect=lambda cur,ref,*args:{'subject_id':ref['subject_id']}):
            a.install(case.plan,case.home,'fake','fake-host',complete,case.fake.encode)
        self.assertEqual(register.call_count,3)
        self.assertTrue(all('subject_type' not in c.kwargs and 'parent_subject' not in c.kwargs for c in register.call_args_list))
        self.assertTrue(all(c.kwargs['description'] for c in register.call_args_list))
        self.assertTrue(alias.called)
        self.assertTrue(any('insert into knowledge.subject_embedding' in c.args[0] for c in cur.execute.call_args_list))

    def test_schema_guard_only_exact_owned_local_pilot(self):
        case=self.fixture(); scope='pilot-area-10'
        plan={**case.plan,'scope':scope,'host':'knowledge-local-'+scope,'container':'lh-knowledge-local-'+scope+'-local'}
        save(case.home/'state/plan.json',plan)
        with patch.object(local_db,'HERE',case.home.parent),patch.object(pg,'connect') as connect:
            # A different home basename must be rejected before a connection.
            with self.assertRaises(Stop): local_db.apply_unit_first_schema(case.home,plan['host'],plan['container'],digest(plan),'postgresql://postgres:localtest@127.0.0.1:5555/postgres')
            self.assertFalse(connect.called)
        with patch.object(local_db,'HERE',case.home.parent),patch.object(pg,'connect') as connect:
            renamed=case.home.parent/scope
            # Mock only path ownership resolution; don't move any actual pilot state.
            with patch.object(local_db,'load',return_value=plan),patch.object(Path,'exists',return_value=True),patch.object(Path,'resolve',return_value=renamed):
                with self.assertRaises(Stop): local_db.apply_unit_first_schema(case.home,plan['host'],plan['container'],digest(plan),'postgresql://remote/db')
                self.assertFalse(connect.called)

    def test_schema_sql_preserves_description_constraint_and_records_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); home=root/'pilot-area-10'; home.mkdir()
            plan={'write_path':2,'scope':'pilot-area-10','host':'knowledge-local-pilot-area-10','container':'lh-knowledge-local-pilot-area-10-local'}
            save(home/'state/plan.json',plan)
            cur=Mock(); conn=MagicMock(); conn.__enter__.return_value=conn
            conn.cursor.return_value.__enter__.return_value=cur
            with patch.object(local_db,'HERE',root),patch.object(pg,'connect',return_value=conn):
                local_db.apply_unit_first_schema(home,plan['host'],plan['container'],digest(plan),'postgresql://postgres:localtest@127.0.0.1:5555/postgres')
            sql=' '.join(c.args[0] for c in cur.execute.call_args_list)
            self.assertIn('subject_type drop not null',sql); self.assertIn('subject_type_fixed',sql)
            self.assertNotIn('subject_description_present',sql)
            self.assertFalse(load(home/'state/unit-first-schema.json')['placeholder_type'])


if __name__=='__main__': unittest.main()
