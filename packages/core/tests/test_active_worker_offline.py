"""Standalone stdlib/fake-only active worker tests. Never invoke through pytest/conftest."""
import offline_guard as test_offline
import host_defaults  # noqa: F401  socket/process guards installed BEFORE application imports
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
import generation
import place_write as w
import active_worker as c
import active_judgements as j
import source_grounding as sg
import digest_driver as dd
import store_pg
store_pg.connect=test_offline.forbidden
from safety import Ledger, Stop, load, save, digest
from model_output import ModelOutputInvalid
from writer_worker import build_request as writer_request
import test_place_write_offline as previous

CFG={'jev_model':'fake-typed','jev_api_key':'FAKE_HOST_ONLY','jev_base_url':'https://invalid.test'}
RAW='배송함은 4도를 유지한다.\nAGENTS source harness RAW_EVIDENCE_SENTINEL'
CAPTURES=[]
WRITER_CAPTURES=[]

def evidence(role):
    unit={'content':'배송함은 4도를 유지한다.','target_expression':'배송함','source_ranges':[[1,1]],'host_id':'PRIVATE_HOST','unit_id':'PRIVATE_UUID'}
    if role=='extract': return {'source':RAW,'source_start':1,'chunk_range':[1,2],'context':{'document_title':'배송함','heading_path':['배송함']},'origin':'document','migration':True,'parent':'PARENT_TRANSCRIPT'}
    if role in ('reextract','domain-name','subject-name'):
        packet={'source':RAW,'unit':unit,'domain':{'name':'물류','description':'물류 업무','aliases':[],'secret':'PRIVATE_DSN'},'existing':None,'failure_reason':'DDD failed','parent':'PARENT_TRANSCRIPT'}
        return packet
    if role=='group-name':
        return {'domains':[{'name':'물류','description':RAW,'secret':'PRIVATE_DSN'},{'name':'재고','description':'재고 업무','aliases':[]}],
                'existing_groups':['인사'],'parent':'PARENT_TRANSCRIPT'}
    return {'domain':'물류','name':'배송함','description':'기존 설명','subject_type':'entity',
            'subjects':[{'name':'배송함','description':'보관 대상','subject_id':'PRIVATE_UUID','parent_name':None}],
            'fragments':[('fragment-ref',RAW,{'evidence_refs':[{'locator':'box.py#L1','quote':RAW,'secret':'PRIVATE_DSN'}],'dsn':'PRIVATE_DSN'})],
            'parent':'PARENT_TRANSCRIPT','secret':'PRIVATE_DSN'}

def reply(role):
    if role=='extract': return {'items':[{'content':'배송함은 4도를 유지한다.','target_expression':'배송함','source_ranges':[[1,1]]}]}
    if role=='reextract': return {'target_expression':'배송함','reason':'소유 객체'}
    if role in ('domain-name','subject-name'): return {'original_expression':'배송함','source_concept':'배송함','name':'물류' if role=='domain-name' else '배송함','description':'원문 업무 대상'}
    if role=='describe': return {'description':'배송함은 원문 보관 대상이다.'}
    if role=='group-name': return {'name':'공급망','description':'물류와 재고를 묶는 업무군'}
    return {'relation':'unknown','description_comparison':'근거 부족','evidence':[]}

class ActiveContracts(unittest.TestCase):
    def test_all_roles_closed_context_and_full_raw_harness_data(self):
        for role in c.ROLES:
            with self.subTest(role=role):
                packet=evidence(role); before=copy.deepcopy(packet)
                request=c.build_request(role,packet,1000000)
                wire=json.dumps(request,ensure_ascii=False)
                for forbidden in ('PRIVATE_HOST','PRIVATE_UUID','PRIVATE_DSN','PARENT_TRANSCRIPT'):
                    self.assertNotIn(forbidden,wire)
                env=json.loads(request['context']['messages'][0]['content'])
                self.assertEqual(env['role'],role)
                self.assertEqual(env['promptVersion'],c.PROMPT_VERSION)
                self.assertIn('RAW_EVIDENCE_SENTINEL',json.dumps(env,ensure_ascii=False))
                self.assertNotIn('RAW_EVIDENCE_SENTINEL',request['context']['systemPrompt'])
                self.assertEqual(packet,before)
                self.assertEqual(c.parse_output(json.dumps(reply(role),ensure_ascii=False),role),reply(role))
                CAPTURES.append({'request':request,'reply':reply(role)})
        # re-ask variants (#20 language, S003 review A shared component): same closed packet plus catalog names and feedback
        packet=dict(evidence('domain-name'),catalog_names=['물류','인사'],language_feedback='E_AREA_LANGUAGE: previous name Logistics',
                    shared_component_feedback='E_DOMAIN_SHARED_COMPONENT: "주문" names a business that only uses this shared component')
        request=c.build_request('domain-name',packet,1000000)
        env=json.loads(request['context']['messages'][0]['content'])
        self.assertEqual(env['data']['shared_component_feedback'],packet['shared_component_feedback'])
        CAPTURES.append({'request':request,'reply':reply('domain-name')})
        original={'fact':'배송함은 4도를 유지한다.','keywords':['온도'],'source_ranges':[[1,1]],'reason':'원문 보존'}
        packet={'source':RAW,'unit':evidence('subject-name')['unit'],
                'chosen_domain':{'name':'물류','description':'물류 업무','aliases':[]},
                'chosen_subject':{'name':'배송함','description':'보관 대상','aliases':[],'subject_id':'PRIVATE_UUID'}}
        WRITER_CAPTURES.append({'request':writer_request('write',packet,1000000),'reply':original})
        packet.update(original_fields_not_evidence=original,errors=[{'code':'E_SENTENCE_UNGROUNDED','detail':'같은 대상 재작성'}])
        WRITER_CAPTURES.append({'request':writer_request('rewrite',packet,1000000),'reply':{'fact':'배송함은 4도를 유지한다.'}})
    def test_domain_merge_preserves_complete_description_evidence(self):
        snapshot={'domains':{'물류':{'description':'기존 영역','description_evidence':[{'name':'배송함','description':'원래 설명','subject_id':'PRIVATE_UUID','evidence':[('fragment-ref',RAW,{'evidence_quote':RAW})]}]},'재고':{'description':'다른 영역'}},'fragments':[('fragment-ref',2,RAW,{'evidence_quote':RAW})]}
        request=c.build_request('merge-luna',snapshot,1000000)
        data=json.loads(request['context']['messages'][0]['content'])['data']
        self.assertEqual(data['domains'][0]['description_evidence'][0]['evidence'][0]['text'],RAW)
        self.assertEqual(data['fragments'][0]['revision'],2)
        self.assertNotIn('PRIVATE_UUID',json.dumps(request))

    def test_binding_changes_with_source_or_controller_identity(self):
        one=evidence('subject-name'); request=c.build_request('subject-name',one,1000000)
        other=copy.deepcopy(one); other['unit']['unit_id']='OTHER_UUID'
        changed=c.build_request('subject-name',other,1000000)
        self.assertNotEqual(request['inputBinding'],changed['inputBinding'])
        self.assertEqual(request['sourceBinding'],changed['sourceBinding'])
        other['source']+='\n추가'
        self.assertNotEqual(request['sourceBinding'],c.build_request('subject-name',other,1000000)['sourceBinding'])
    def test_bound_unknown_and_output_authority_no_silent_truncation(self):
        for role in c.ROLES:
            with self.subTest(role=role):
                with self.assertRaises(c.WorkerFailure): c.build_request(role,evidence(role),5)
                for bad in ('null','[]','{}','{"x":NaN}','{"x":1,"x":2}',json.dumps({**reply(role),'subject_id':'forged'})):
                    with self.assertRaises(ModelOutputInvalid): c.parse_output(bad,role)
        with self.assertRaises(c.WorkerFailure): c.build_request('task text',{},999999)
        self.assertEqual(c.parse_output('{"relation":"unknown","description_comparison":"불명확","evidence":[]}','merge-luna')['relation'],'unknown')
    def test_extract_split_bounded_and_unknown_stop_not_format_error(self):
        attempts=[]
        def worker(role,packet):
            attempts.append(packet['chunk_range'])
            raise ModelOutputInvalid('journaled invalid')
        ports=w.Ports(test_offline.forbidden,lambda xs:[],test_offline.forbidden,test_offline.forbidden,worker=worker)
        items=w.judge_input(ports,'synthetic','one\ntwo\nthree\nfour',origin='document',migration=True)
        self.assertTrue(all(row.get('judge_partial') for row in items))
        self.assertLessEqual(len(attempts),7)
        attempts.clear()
        def stopped(role,packet): attempts.append(1); raise Stop('uncertain reservation')
        ports=w.Ports(test_offline.forbidden,lambda xs:[],test_offline.forbidden,test_offline.forbidden,worker=stopped)
        with self.assertRaises(Stop): w.judge_input(ports,'synthetic','one\ntwo',origin='document')
        self.assertEqual(len(attempts),1)


class TypedWire(unittest.TestCase):
    def test_selection_kind_grounding_and_identity_final_http(self):
        observed=[]
        def http(request,**kw):
            body=json.loads(request.data); observed.append(body); j.verify_body(body)
            self.assertEqual(request.full_url,CFG['jev_base_url']+'/v1/systemone')
            self.assertNotIn('FAKE_HOST_ONLY',json.dumps(body))
            answers={k:({'probabilities':{'fact':.99}} if q['type']=='choice' else {'noul':.9}) for k,q in body['questions'].items()}
            return io.StringIO(json.dumps({'answers':answers}))
        choose=j.make_choice(CFG,http=http)
        choose({'unit':evidence('subject-name')['unit'],'sentence':'배송함은 보관한다.','fields':{'fact':'배송함은 보관한다.','subject_id':'PRIVATE_UUID'},'memory':'PARENT_TRANSCRIPT'},['unit'],'items[{i}] 종류',{'fact':'사실'})
        for question in (sg.SENTENCE_QUESTION,sg.NAMES_QUESTION):
            ask=j.make_ask(CFG,'grounding',http=http)
            ask({'source':RAW,'source_start_line':1,'grounding_version':5,'checks':{'0':{'sentence':'배송함은 보관한다.','excluded_names':['배송함'],'candidate_names':[],'memory':'PARENT_TRANSCRIPT'}}},['0'],question)
        ask=j.make_ask(CFG,'maintenance-identity',http=http)
        ask({'comparison':{'subjects':[{'name':'배송함','description':'보관 대상','type':None,'parent':None,'secret':'PRIVATE_DSN'}]}},['compare'],'items[{i}] 같은 객체')
        self.assertEqual(len(observed),4)
        self.assertEqual(observed[1]['questions']['m0']['instructions'],sg.SENTENCE_QUESTION.format(i=0))
        self.assertNotIn('PARENT_TRANSCRIPT',json.dumps(observed))
        self.assertNotIn('PRIVATE_DSN',json.dumps(observed))
        self.assertNotIn('PRIVATE_UUID',json.dumps(observed))
        self.assertIn('RAW_EVIDENCE_SENTINEL',json.dumps(observed,ensure_ascii=False))
    def test_typed_selection_binding_preserves_opaque_controller_identity(self):
        raw={'model':'fake','state':{'unit':evidence('subject-name')['unit']},'questions':{'q':{'type':'noul','instructions':'기존 질문','criteria':{'true':'예','false':'아니오'}}}}
        first=j.bind_body('selection',raw)
        raw['state']['unit']['unit_id']='DIFFERENT_UUID'
        second=j.bind_body('selection',raw)
        self.assertNotEqual(first['state']['workerContract']['inputBinding'],second['state']['workerContract']['inputBinding'])
        self.assertNotIn('DIFFERENT_UUID',json.dumps(second))
        self.assertEqual(first['state']['workerContract']['sourceBinding'],second['state']['workerContract']['sourceBinding'])

    def test_forged_stale_typed_binding(self):
        body=j.bind_body('worktime-actor',{'model':'fake','state':{'narrative':'원문','candidate_fact':'배송함은 보관한다.','evidence_quote':RAW,'existing_records_excerpt':'없음'},'questions':{'q':{'type':'noul','instructions':'기존 질문','criteria':{'true':'예','false':'아니오'}}}})
        j.verify_body(body)
        for mutate in (lambda b:b['state'].update(candidate_fact='다른 대상'),lambda b:b['state']['workerContract'].update(binding='forged'),lambda b:b['questions']['q'].update(instructions='다른 질문')):
            other=copy.deepcopy(body); mutate(other)
            with self.assertRaises(c.WorkerFailure): j.verify_body(other)
    def test_actual_digest_runner_final_http_and_receipt_packet(self):
        import worktime_driver
        fact={'fact':'배송함은 보관한다.','operation':'add','reason':'원문','evidence_quote':RAW,'evidence_refs':[{'quote':RAW}]}
        packet=worktime_driver.build_packet(fact,'기존 기록 없음')
        packet['state']['memory']='PARENT_TRANSCRIPT'
        observed=[]
        def http(request,**kw):
            body=json.loads(request.data); j.verify_body(body); observed.append(body)
            return io.StringIO(json.dumps({'answers':{k:{'noul':.9} for k in body['questions']},'model':'fake-actual','usage':{'input_tokens':10,'output_tokens':1,'cost':.01}}))
        with patch.object(host_defaults,'_installed_judgement_http',http):
            result=j.make_actor_judge(CFG)(packet)
        self.assertEqual(result['jev_model_actual'],'fake-actual')
        self.assertNotIn('workerContract',packet['state'])  # DB packet equality/cache ownership is unchanged.
        self.assertNotIn('memory',observed[0]['state'])
        self.assertEqual(result['worker_contract'],observed[0]['state']['workerContract'])
        self.assertEqual(packet['template_id'],'record-need')
        self.assertEqual(observed[0]['questions'],packet['questions'])
        self.assertEqual(result['cost_usd'],.01)

    def test_actor_add_update_deprecate_templates_and_packet_cache_parity(self):
        import worktime_driver
        captured=[]
        def judge(packet,*,cfg,request_projector):
            import runner
            original=copy.deepcopy(packet)
            final=request_projector(runner.build_request({**packet,'model':cfg['jev_model']},'state'))
            j.verify_body(final); captured.append(final)
            self.assertEqual(packet,original)
            self.assertEqual(final['questions'],packet['questions'])
            return {'answers':{k:{'noul':.9} for k in packet['questions']}}
        for operation in ('add','update','deprecate'):
            packet=worktime_driver.build_packet({'fact':'배송함은 보관한다.','operation':operation,'reason':'근거','evidence_quote':RAW},'현재 대상')
            before=copy.deepcopy(packet)
            result=j.make_actor_judge(CFG,judge=judge)(packet)
            self.assertEqual(packet,before)
            self.assertEqual(result['worker_contract']['template'],{'template_id':packet['template_id'],'template_version':packet['template_version']})
        self.assertEqual(len(captured),3)
        self.assertEqual(captured[-1]['state']['deprecate_reason'],'배송함은 보관한다.')

class NamingProposalContract(unittest.TestCase):
    def test_closed_context_and_existing_precondition(self):
        for role in ('domain-name','subject-name'):
            original=evidence(role);variants=[]
            for value in (None,[],True,{},c.naming_proposal_context(role.split('-')[0]),{'final_admission':'allowed'}):
                bad=copy.deepcopy(original);bad['naming_context']=value;variants.append(bad)
            bad=copy.deepcopy(original);bad.pop('existing');variants.append(bad)
            for value in ({'name':'기존대상'},True,False,'none'):
                bad=copy.deepcopy(original);bad['existing']=value;variants.append(bad)
            for bad in variants:
                with self.subTest(role=role,packet=bad),self.assertRaises(c.WorkerFailure):
                    c.build_request(role,bad,1000000)

    def test_explicit_phase_bound_without_source_absence_claim(self):
        for role in ('domain-name','subject-name'):
            packet=evidence(role);before=copy.deepcopy(packet)
            request=c.build_request(role,packet,1000000)
            env=json.loads(request['context']['messages'][0]['content'])
            self.assertEqual(env['data']['naming_context'],c.naming_proposal_context(role.split('-')[0]))
            self.assertEqual(env['data']['source_raw'],RAW)
            self.assertNotIn('PARENT_TRANSCRIPT',json.dumps(env))
            self.assertIn('final_admission=pending',request['context']['systemPrompt'])
            self.assertIn('원문에 기존',request['context']['systemPrompt'])
            self.assertEqual(packet,before)
            self.assertEqual(request['schemaVersion'],'active-worker/1')
            self.assertEqual(env['promptVersion'],'active-worker-prompt/12')
        first=c.naming_proposal_context('domain');first['final_admission']='allowed'
        self.assertEqual(c.naming_proposal_context('domain')['final_admission'],'pending')
        with self.assertRaises(c.WorkerFailure):c.naming_proposal_context('write')


    def _domain(self,final_none,*,coverage=.01,reuse=False):
        events=[];calls=[];raw='배송함은 배송품을 보관한다.'
        unit={'content':raw,'source_ranges':[[1,1]],'target_expression':'배송함'}
        catalog=[{'name':'재고','description':'재고 업무 영역'},{'name':'인사','description':'인사 업무 영역'}]
        if reuse:catalog=[{'name':'배송함','description':'이미 검토된 기존 영역'}]
        def choose(kind,packet,options):
            full='0' in options
            calls.append(full);events.append('final-choice' if full else 'search')
            none=final_none if full else (.57 if len(calls)==1 else .50)
            n=len(options)-1
            return {k:none if k=='none' else (1-none)/n for k in options}
        def ask(state,labels,question):
            if question==w.DOMAIN_QUESTION:return [.99]
            if question==w.DOMAIN_MEMBERSHIP_QUESTION:return [.29]
            if question==w.DOMAIN_COVERED_QUESTION:events.append('coverage');return [coverage]
            if question==w.DOMAIN_OVERLAP_QUESTION:events.append('overlap');return [.01]  # Task382 reverse containment
            if question==w.DOMAIN_SAME_QUESTION:
                return [.29 if state['proposed']['name']==unit['target_expression'] else .01]
            raise AssertionError('unexpected question '+question)
        def worker(role,packet):
            events.append('name');self.assertEqual(role,'domain-name')
            request=c.build_request(role,packet,1000000)
            env=json.loads(request['context']['messages'][0]['content'])
            self.assertEqual(env['data']['naming_context']['final_admission'],'pending')
            self.assertEqual(env['data']['source_raw'],raw)
            return {'original_expression':'배송함','source_concept':'배송함','name':'물류','description':'배송품 보관과 배송 업무 영역'}
        ports=w.Ports(test_offline.forbidden,lambda texts:[[0.,0.] for _ in texts],choose,ask,worker=worker)
        return lambda:w.place_identity('domain',unit,None,catalog,ports,5,document=raw),events

    def test_weak_search_names_candidate_before_final_none_hold(self):
        run,events=self._domain(.50)
        with self.assertRaisesRegex(w.PlacementHeld,'requires explicit Jev none'):run()
        self.assertLess(events.index('name'),events.index('coverage'))
        self.assertLess(events.index('coverage'),events.index('final-choice'))
        self.assertEqual(events.count('name'),1)

    def test_same_weak_search_can_finish_after_explicit_final_none(self):
        run,events=self._domain(.99)
        result=run()
        self.assertEqual(result['chosen']['name'],'물류')
        self.assertLess(events.index('name'),events.index('final-choice'))

    def test_coverage_hold_is_not_bypassed_by_candidate_name(self):
        run,events=self._domain(.99,coverage=.4)
        with self.assertRaisesRegex(w.PlacementHeld,'coverage is not clearly no'):run()
        self.assertIn('name',events);self.assertNotIn('final-choice',events)

    def test_existing_exact_match_never_invokes_naming(self):
        run,events=self._domain(.99,reuse=True)
        self.assertEqual(run()['chosen']['name'],'배송함')
        self.assertNotIn('name',events)

class ActualController(unittest.TestCase):
    pass  # all cases moved to packages/eval/tests (issue #6 step 7)

class DomainContextWire(unittest.TestCase):
    def run_context(self,none=.9):
        captured=[];packets=[];unit=evidence('domain-name')['unit']
        def http(request,**kw):
            body=json.loads(request.data);j.verify_body(body);captured.append(body)
            answers={}
            for key,q in body['questions'].items():
                if q['type']=='choice':
                    n=none if all(k=='none' or k.isdigit() for k in q['criteria']) else .54
                    answers[key]={'type':'choice','probabilities':{k:n if k=='none' else (1-n)/(len(q['criteria'])-1) for k in q['criteria']}}
                else:answers[key]={'type':'noul','noul':.64 if q['instructions']==w.DOMAIN_QUESTION else .23 if q['instructions']==w.DOMAIN_COVERED_QUESTION else .21}
            return io.StringIO(json.dumps({'answers':answers}))
        judge=j.make_choice(CFG,http=http);ask=j.make_ask(CFG,'placement',http=http)
        def choose(kind,state,options):return judge(state,['unit'],'UNCHANGED domain choice items[{i}]',options)[0]['probabilities']
        def worker(role,packet):
            packets.append(copy.deepcopy(packet))
            return {'original_expression':'배송함','source_concept':'배송함','name':'보관관리','description':'보관 업무 영역'}
        ports=w.Ports(Mock(side_effect=AssertionError('legacy model forbidden')),lambda texts:[[1.,0.] for _ in texts],choose,ask,worker=worker)
        catalog=[{'name':'물류','description':'물류 업무','aliases':[]},{'name':'기억','description':'기억 업무','aliases':[]}]
        try:result=w.place_identity('domain',unit,None,catalog,ports,5,document=RAW)
        except w.PlacementHeld:
            if none>=.7:raise
            result=None
        return result,captured,packets
    def test_context_survives_actual_typed_selection_and_placement_http(self):
        result,bodies,packets=self.run_context();self.assertTrue(result['new'])
        self.assertEqual({b['state']['workerContract']['role'] for b in bodies},{'selection','placement'})
        for body in bodies:
            state=body['state'];self.assertEqual(state['source'],RAW);self.assertEqual(state['source_start_line'],1)
            self.assertEqual(state['unit'],c.project(evidence('domain-name')['unit'],c.UNIT))
            meta=state['workerContract'];self.assertEqual(meta['schemaVersion'],'active-jev/1');self.assertEqual(meta['promptVersion'],'active-jev-criteria/1')
            self.assertNotIn('PRIVATE_HOST',json.dumps(body));self.assertNotIn('PRIVATE_UUID',json.dumps(body))
            j.verify_body(body)
        # Task363-N is on by default since #20 (Ports.area_naming_context): domain naming also gets catalog_names.
        self.assertEqual(set(packets[0]),{'source','unit','domain','existing','catalog_names'})  # #20: catalog names by default
        self.assertEqual(packets[0]['source'],RAW);self.assertNotIn('naming_context',packets[0])
    def test_changed_source_or_target_cannot_reuse_the_old_typed_binding(self):
        _,bodies,_=self.run_context()
        for mutate in (lambda b:b['state'].update(source=RAW+' changed'),lambda b:b['state'].update(source_start_line=2),
                       lambda b:b['state']['unit'].update(target_expression='other')):
            changed=copy.deepcopy(bodies[-1]);mutate(changed)
            with self.assertRaises(c.WorkerFailure):j.verify_body(changed)
        old=copy.deepcopy(bodies[-1]);old['state'].pop('workerContract');old['state'].pop('source');old['state'].pop('source_start_line')
        rebound=j.bind_body('selection',old)
        self.assertNotEqual(rebound['state']['workerContract']['sourceBinding'],bodies[-1]['state']['workerContract']['sourceBinding'])
        self.assertNotEqual(rebound['state']['workerContract']['inputBinding'],bodies[-1]['state']['workerContract']['inputBinding'])
    def test_058_stays_held_with_full_context_and_no_write_role(self):
        result,bodies,packets=self.run_context(.58);self.assertIsNone(result)
        self.assertEqual(len(packets),1);self.assertEqual(bodies[-1]['questions']['m0']['criteria']['none'],'어느 기존 도메인(맥락)에도 속하지 않음')
        self.assertEqual(bodies[-1]['state']['source'],RAW)


if __name__=='__main__':
    # Optional explicit fresh artifact path exports real Python-built packets for the SDK fake-wire test.
    capture=None
    if '--capture-root' in sys.argv:
        i=sys.argv.index('--capture-root'); capture=Path(sys.argv[i+1]); del sys.argv[i:i+2]
    result=unittest.main(exit=False,verbosity=2).result
    if capture is not None:
        capture.mkdir(parents=True,exist_ok=True)
        save(capture/'active-caller-capture.json',CAPTURES)
        save(capture/'active-writer-capture.json',WRITER_CAPTURES)
    sys.exit(0 if result.wasSuccessful() else 1)
