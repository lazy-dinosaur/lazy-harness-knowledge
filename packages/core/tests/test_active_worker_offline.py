"""Standalone stdlib/fake-only active worker tests. Never invoke through pytest/conftest."""
import test_offline  # socket/process guards installed BEFORE application imports
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
    return {'domain':'물류','name':'배송함','description':'기존 설명','subject_type':'entity',
            'subjects':[{'name':'배송함','description':'보관 대상','subject_id':'PRIVATE_UUID','parent_name':None}],
            'fragments':[('fragment-ref',RAW,{'evidence_refs':[{'locator':'box.py#L1','quote':RAW,'secret':'PRIVATE_DSN'}],'dsn':'PRIVATE_DSN'})],
            'parent':'PARENT_TRANSCRIPT','secret':'PRIVATE_DSN'}

def reply(role):
    if role=='extract': return {'items':[{'content':'배송함은 4도를 유지한다.','target_expression':'배송함','source_ranges':[[1,1]]}]}
    if role=='reextract': return {'target_expression':'배송함','reason':'소유 객체'}
    if role in ('domain-name','subject-name'): return {'original_expression':'배송함','source_concept':'배송함','name':'물류' if role=='domain-name' else '배송함','description':'원문 업무 대상'}
    if role=='describe': return {'description':'배송함은 원문 보관 대상이다.'}
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
    def test_malformed_extract_is_done_before_split_and_uncertain_never_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            paid=Mock(scope='extract'); paid.ledger=Ledger(Path(tmp)/'paid.jsonl','fresh',{'generation':10,'maintenance':10})
            sent=[]
            def send(request): sent.append(request); return {'ok':True,'text':'broken JSON','cost':0.01}
            worker=t.make_worker(paid,inspect=lambda:{'ok':True,'inputBudget':1000000},send=send)
            for _ in range(2):
                with self.assertRaises(ModelOutputInvalid): worker('extract',evidence('extract'))
            self.assertEqual(len(sent),1)
            self.assertEqual(paid.ledger.rows[-1]['state'],'done')
            paid.scope='uncertain'; sent.clear()
            def uncertain(request): sent.append(request); return {'ok':False,'code':'E_WRITER_AUTH'}
            worker=t.make_worker(paid,inspect=lambda:{'ok':True,'inputBudget':1000000},send=uncertain)
            for _ in range(2):
                with self.assertRaises(Stop): worker('extract',evidence('extract'))
            self.assertEqual(len(sent),1)
            self.assertEqual(paid.ledger.rows[-1]['state'],'reserved')
    def test_generation_vs_maintenance_and_precompute_text_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            paid=Mock(scope='fresh'); paid.ledger=Ledger(Path(tmp)/'paid.jsonl','fresh',{'generation':10,'maintenance':10})
            worker=t.make_worker(paid,inspect=lambda:{'ok':True,'inputBudget':1000000},send=lambda r:{'ok':True,'text':json.dumps(reply(r['role'])),'cost':0.01})
            for role in c.ROLES: worker(role,evidence(role))
            done=[r for r in paid.ledger.rows if r['state']=='done']
            self.assertEqual([r['step'] for r in done],['generation']*4+['maintenance']*2)
            self.assertTrue(all(r['account']=='subscription' and r['model']=='Luna' for r in done))
            for row in done:
                result=load(Path(tmp)/row['result'])
                self.assertIsInstance(result['text'],str)
                self.assertEqual(digest(result),row['sha'])
                self.assertEqual(row['request']['inputBinding'],result['inputBinding'])
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
        with patch.object(dd,'urlopen',http):
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
            self.assertEqual(env['promptVersion'],'active-worker-prompt/7')
        first=c.naming_proposal_context('domain');first['final_admission']='allowed'
        self.assertEqual(c.naming_proposal_context('domain')['final_admission'],'pending')
        with self.assertRaises(c.WorkerFailure):c.naming_proposal_context('write')

    def test_source_error_is_settled_but_not_replayed_or_success(self):
        raw='{"error":{"code":"E_SOURCE","detail":"자료에서 기존 업무 영역이 명시적으로 none이라고 확인되지 않습니다."}}'
        for role in ('domain-name','subject-name'):
            with tempfile.TemporaryDirectory() as tmp:
                paid=Mock(scope=role);paid.ledger=Ledger(Path(tmp)/'paid.jsonl','synthetic-naming',{'generation':10})
                sent=[]
                def send(request):sent.append(request);return {'ok':True,'text':raw,'cost':.01}
                worker=t.make_worker(paid,inspect=lambda:{'ok':True,'inputBudget':1000000},send=send)
                for _ in range(2):
                    with self.assertRaises(ModelOutputInvalid):worker(role,evidence(role))
                self.assertEqual(len(sent),1)
                self.assertEqual(paid.ledger.rows[-1]['state'],'done')

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
        # Task363-N is opt-in (Ports.area_naming_context); default packets stay the closed four keys.
        self.assertEqual(set(packets[0]),{'source','unit','domain','existing'})
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
