"""Typed unit-first acceptance; guard installed before any application imports."""
import generation
import offline_guard as test_offline  # noqa: F401  socket/process guards installed BEFORE application imports
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import place_write as w
import place_write_absorb as a
import source_grounding as sg
import store_pg as pg
import precompute
from safety import Stop, digest, load, save


class Fake:
    def __init__(self, source, *, leak=False, repair=True):
        self.source=source; self.calls=[]; self.choices=[]; self.questions=[]
        self.leak=leak; self.repair=repair
    def model(self, name: str, packet: dict, task: str) -> dict:
        self.calls.append((name,copy.deepcopy(packet),task))
        if name.startswith('judge-'):
            return {'items':[{'content':line,'source_ranges':[[i,i]],'target_expression':'Context retrieval' if 'Context' in line else '일정표'} for i,line in enumerate(self.source.splitlines(),1) if line and not line.startswith('#')]}
        if name.startswith(('domain-','subject-')):
            is_domain=name.startswith('domain-'); content=packet['unit']['content']
            existing=packet['existing']
            if is_domain:
                return {'original_expression':'검색' if 'Context' in content else '주문관리파트','source_concept':'검색','name':'검색','description':'원문 검색 업무 영역'}
            return {'original_expression':'Context retrieval' if 'Context' in content else '일정표','source_concept':'Context retrieval' if 'Context' in content else '일정표',
                    'name':'맥락 조회' if 'Context' in content else '예약 시트','description':'기록 맥락 조회 대상' if 'Context' in content else '예약 시트 대상'}
        subject=packet['chosen_subject']['name']; unit=packet['unit']
        sentence=subject+'는 Context retrieval로 기록을 조회한다.' if 'Context' in unit['content'] else subject+' 시간은 환경설정의 포장단위에서 정해진다.'
        if self.leak and 'Context' in unit['content'] and (not name.startswith('rewrite-') or not self.repair):
            sentence='주문관리의 AI상담 '+sentence
        if name.startswith('rewrite-'): return {'fact':sentence}  # delta merge retains reason/ranges
        return {'fact':sentence,'kind':'fact','keywords':['조회' if 'Context' in unit['content'] else '예약'],
                'reason':'원문 업무 규칙 보존','source_ranges':unit['source_ranges']}
    def encode(self, texts: list[str]) -> list[list[float]]:
        # Intentionally all similarities zero: top-K has NO cosine admission floor.
        return [[0.,0.] for _ in texts]
    def choose(self, kind: str, packet: dict, options: dict[str,str]) -> dict[str,float]:
        self.choices.append((kind,copy.deepcopy(packet),options))
        if kind == 'kind': return {k:.99 if k=='fact' else .001 for k in options}
        # Task377 stage-0 repair: source_modality v3 (fidelity + modality) judgements added after this Fake was written.
        if kind == 'fidelity': return {'supported':.98,'mismatch':.01,'uncertain':.01}
        if kind == 'modality':
            mode={'fact':'description','constraint':'obligation','term':'definition'}.get(packet.get('written_kind'),'other')
            return {k:(.93 if k==mode else .01) for k in options}
        candidates=packet['candidates']; content=packet['unit']['content']
        wanted='검색' if kind=='domain' and 'Context' in content else '주문관리' if kind=='domain' else '맥락 조회' if 'Context' in content else '예약 시트'
        key=next((r['key'] for r in candidates if r['name']==wanted),'none')
        return {k:.99 if k==key else .001 for k in options}
    def ask(self, state: dict, texts: list[str], question: str) -> list[float]:
        self.questions.append((copy.deepcopy(state),texts,question))
        if question==w.DDD2: return [.98]
        if question==w.DOMAIN_QUESTION: return [.99]
        if question==w.DOMAIN_MEMBERSHIP_QUESTION: return [.01]
        if question==w.DOMAIN_OVERLAP_QUESTION: return [.01 for _ in texts] if texts else [.01]  # Task382  # Task377 stage-0 repair: ambiguous-top membership added later
        if question in (w.SAME_QUESTION,w.DOMAIN_SAME_QUESTION,w.DOMAIN_COVERED_QUESTION): return [.01]
        if question==sg.SENTENCE_QUESTION: return [.97 for _ in texts]
        if question==sg.NAMES_QUESTION: return [.99 if any(n in state['checks'][t]['sentence'] for n in ('AI상담','주문관리')) else .01 for t in texts]
        if question==sg.SPAN_QUESTION: return [.99 if t in ('AI상담','주문관리') else .01 for t in texts]
        raise AssertionError('new path called subject-name grounding')
    def ports(self) -> w.Ports: return w.Ports(self.model,self.encode,self.choose,self.ask)


class UnitFirst(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup); self.home=Path(self.tmp.name)
        self.text='주문관리파트의 일정표 시간은 환경설정의 포장단위에서 정해진다.\n주문관리파트의 일정표는 환경설정의 포장단위를 공유한다.\n검색은 Context retrieval로 기록을 조회한다.\n'
        path=self.home/'v1/record.md'; path.parent.mkdir(); path.write_text(self.text)
        self.r={'record_id':'R05','path':'record.md','sha256':hashlib.sha256(self.text.encode()).hexdigest()}
        self.plan={'write_path':2,'host':'fake-host','records':[self.r],'candidate_policy':{'top_k':5},'area_catalog':True,'embedding_precompute':True,'fresh_database':True}
        save(self.home/'state/plan.json',self.plan)
        save(self.home/'state/area-catalog.json',{'confirmed_by_user':True,'plan_sha':digest(self.plan),'areas':[{'name':'주문관리','description':'예약을 다루는 업무 영역'}]})
        self.paid=Mock(); self.fake=Fake(self.text)
        patcher=patch.object(generation,'HERE',self.home); patcher.start(); self.addCleanup(patcher.stop)
    def run_phases(self, fake=None):
        fake=fake or self.fake
        for phase in ('judge','place','write','ground'):
            result=w.run(phase,self.plan,self.home,self.paid,fake.ports())
        return result
    def test_judge_enumerates_all_without_names_and_no_importance_filter(self):
        report=w.run('judge',self.plan,self.home,self.paid,self.fake.ports())
        self.assertEqual(len(report['units']),3)
        saved=load(self.home/'state/place-write/judge/R05.json')['result']
        self.assertEqual(set(saved[0]),{'content','source_ranges','target_expression'})
        self.assertEqual([r['content'] for r in saved],self.text.splitlines())
        self.assertEqual([r['source_ranges'] for r in saved],[[[1,1]],[[2,2]],[[3,3]]])
        self.assertTrue(self.fake.calls[0][1]['migration'])
        self.assertIn('빠짐없이',self.fake.calls[0][2])
    def test_any_input_judge_and_migration_semantics(self):
        for origin in ('work-result','human-input','document'):
            fake=Fake(self.text)
            items=w.judge_input(fake.ports(),'general',self.text,origin=origin)
            self.assertEqual(len(items),3)
            self.assertFalse(fake.calls[0][1]['migration'])
            self.assertEqual(fake.calls[0][1]['origin'],origin)
    def test_top_k_is_ranked_and_never_cosine_filtered(self):
        catalog=[{'name':'D'+str(i),'description':'영역'} for i in range(9)]
        rows=w.ranked('domain',{'content':'내용','target_expression':'대상'},catalog,self.fake.ports(),5)
        self.assertEqual(len(rows),5); self.assertEqual([r['rank'] for r in rows],[1,2,3,4,5])
        self.assertTrue(all(r['similarity']==0 for r in rows))
    def test_invalid_citations_are_rejected_at_judge(self):
        for bounds in ([],[[0,1]],[[1,99]],[[True,1]],[[2,1]]):
            with self.assertRaises(Stop): w.judge_items({'items':[{'content':'x','source_ranges':bounds}]},self.text)
    def test_grounding_repairs_range_from_whole_original_and_preserves_history(self):
        self.run_phases(); row=w.assert_completed(self.plan,self.home)['units'][0]
        # Task377 stage-0 repair: a unit-first fact keeps its exact extracted evidence (source_grounding.repair_citations
        # 'preserved-source-unit'); the lexical locator may only repair a fact without that unit evidence.
        f=copy.deepcopy(row['fact']); kept=copy.deepcopy(f['source_ranges']); result={'sentence':{'status':'jev-pass','support_ranges':[[2,2]]}}
        self.assertFalse(sg.repair_citations(f,result,self.text))
        self.assertEqual(result['sentence']['citation_status'],'preserved-source-unit'); self.assertEqual(f['source_ranges'],kept)
        f=copy.deepcopy(row['fact']); f.pop('source_unit',None); old=copy.deepcopy(f['evidence_refs'])
        self.assertTrue(sg.repair_citations(f,{'sentence':{'status':'jev-pass','support_ranges':[[2,2]]}},self.text))
        self.assertEqual(f['source_ranges'],[[2,2]])
        self.assertEqual(f['source_reference_history'][-1]['evidence_refs'],old)
    def test_absorb_refuses_unfinished_ground_before_db_effect(self):
        with patch.object(pg,'connect',side_effect=AssertionError('DB forbidden')):
            with self.assertRaises(Stop): a.absorb(self.plan,self.home,'never',self.paid,{},'h')
    def test_uncertain_domain_requires_explicit_none_before_creation(self):
        ports=w.Ports(self.fake.model,self.fake.encode,lambda kind,packet,options:{k:.5 for k in options},self.fake.ask)
        w.run('judge',self.plan,self.home,self.paid,ports)
        result=w.run('place',self.plan,self.home,self.paid,ports)
        self.assertEqual(len(result['units']),3)
        self.assertEqual(len(result['units'][0]['domain']['searches'][0]['candidates']),1)
        # Task377 stage-0 repair: domains now use the membership question (ambiguous_domain_membership), subjects keep same-meaning.
        self.assertTrue(result['units'][0]['domain']['ambiguous_domain_membership']['score'] < .5)
        self.assertTrue(all('explicit Jev none' in str(r['held_reasons']) for r in result['units']))
        w.run('write',self.plan,self.home,self.paid,ports)
        w.run('ground',self.plan,self.home,self.paid,ports)
        self.assertFalse(any('uncertain' in str(r.get('held_reasons')) for r in result['units']))

    def test_judge_rejects_subject_fact_domain_fields_and_holds_partial(self):
        with self.assertRaises(Stop):
            w.judge_items({'items':[{'content':'x','source_ranges':[[1,1]],'subject':'누출'}]},self.text)
        held=w.judge_items({'partial':True,'items':[]},self.text)
        self.assertEqual(held[0]['source_ranges'],[[1,3]])
        self.assertTrue(held[0]['judge_partial'])
    def test_per_unit_existing_and_new_domain_subject_reuse_no_floor(self):
        result=self.run_phases(); rows=result['units']
        self.assertEqual([r['domain']['chosen']['name'] for r in rows],['주문관리','주문관리','검색'])
        self.assertEqual([r['domain']['new'] for r in rows],[False,False,True])
        self.assertEqual([r['subject']['new'] for r in rows],[True,False,True])
        self.assertEqual(rows[1]['subject']['candidates'][0]['rank'],1)
        self.assertIsNone(rows[1]['subject']['candidates'][0]['similarity'])
        self.assertEqual(rows[1]['subject']['searches'][0]['query_kind'],'exact-name-or-alias')
        self.assertEqual([d['name'] for d in result['new_domains']],['검색'])
        # Task377 stage-0 repair: source_modality fidelity/modality choices have no 'none'; placement choices do.
        self.assertTrue(all('none' in c[2] for c in self.fake.choices if c[0] not in ('kind','fidelity','modality')))
    def test_matched_names_sentence_alias_and_no_foreign_link(self):
        rows=self.run_phases()['units']; f=rows[0]['fact']
        self.assertEqual(f['domain'],'주문관리'); self.assertIn('예약 시트',f['fact'])
        self.assertNotIn('주문관리파트',f['fact']); self.assertNotIn('일정표',f['fact'])
        self.assertNotIn('주문관리파트',rows[0]['domain']['chosen']['aliases'])  # no regenerated domain expression
        self.assertIn('일정표',f['aliases']); self.assertIn('환경설정',f['fact'])
        record=Mock(return_value={'entry_id':'fake-entry'})
        a.record_unit(rows[0]['unit_id'],f,record,'fake-host',lambda *args:[])
        self.assertFalse(record.call_args.kwargs['leaf_references'])
        self.assertTrue(record.call_args.kwargs['fixed_subject'])
        self.assertNotIn('leaf_links',record.call_args.args[0]['facts'][0])
    def test_no_generation_naming_refix_shorten_or_subject_name_pass(self):
        with patch.object(sg,'check_names',side_effect=AssertionError('name-pass forbidden')), \
             patch('objects.object_map',side_effect=AssertionError('old identity map forbidden')), \
             patch('objects.reuse_metadata',side_effect=AssertionError('old identity correction forbidden')):
            result=self.run_phases()
        self.assertFalse(any(r['held_reasons'] for r in result['units']))
        self.assertEqual({q[2] for q in self.fake.questions},{w.DDD2,w.DOMAIN_QUESTION,w.DOMAIN_SAME_QUESTION,w.DOMAIN_COVERED_QUESTION,w.DOMAIN_OVERLAP_QUESTION,sg.SENTENCE_QUESTION,sg.NAMES_QUESTION})
    def test_leak_q1_pass_q2_fail_rewritten_once_with_fixed_subject_and_delta_merge(self):
        fake=Fake(self.text,leak=True,repair=True); rows=self.run_phases(fake)['units']; r=rows[2]
        self.assertEqual(r['initial']['q1']['score'],.97); self.assertEqual(r['initial']['q2']['status'],'held')
        self.assertTrue(r['rewritten']); self.assertFalse(r['held_reasons'])
        self.assertNotIn('AI상담',r['fact']['fact']); self.assertIn('맥락 조회',r['fact']['fact'])
        self.assertEqual(r['fact']['reason'],'원문 업무 규칙 보존')
        self.assertEqual(sum(c[0].startswith('rewrite-') for c in fake.calls),1)
    def test_persistent_leak_held_not_stored_other_units_survive(self):
        result=self.run_phases(Fake(self.text,leak=True,repair=False)); row=result['units'][2]
        self.assertTrue(row['held_reasons']); self.assertEqual(row['final']['q2']['status'],'held')
        g=load(self.home/'state/generated/R05.json')
        self.assertEqual(len(g['facts']),2); self.assertEqual(len(g['held_units']),1)
        self.assertEqual(a.unit_report(self.plan,self.home)['held_units'],1)
    def test_sentence_requires_verbatim_subject_even_when_alias_present(self):
        self.run_phases(); row=w.assert_completed(self.plan,self.home)['units'][0]
        fact='일정표 시간은 환경설정에서 정해진다.'
        with self.assertRaisesRegex(ValueError,'subject verbatim'):
            w.validate_fact(self.r,self.home,row,row,{'fact':fact,'kind':'fact','_source_modality':self.modality(row,fact)})
    def test_rewrite_cannot_rename_subject(self):
        row=self.run_phases()['units'][0]
        fact='새 이름은 동작한다.'
        with self.assertRaisesRegex(ValueError,'cannot rename'):
            w.validate_fact(self.r,self.home,row,row,{'subject':'새 이름','fact':fact,'kind':'fact','_source_modality':self.modality(row,fact)},row['written'])
    def modality(self, row, fact, kind='fact'):
        # Task377 stage-0 repair: validate_fact checks the source-modality receipt first; give a valid one for the
        # new sentence so the test still reaches the rename / verbatim-subject guard it protects.
        import source_modality as sm
        return sm.review(self.fake.choose,row,fact,kind,w.source(self.r,self.home))
    def test_resume_idempotence_and_receipt_drift(self):
        first=self.run_phases(); count=len(self.fake.calls); asks=len(self.fake.questions)
        second=self.run_phases()
        self.assertEqual(first,second); self.assertEqual(len(self.fake.calls),count); self.assertEqual(len(self.fake.questions),asks)
        row=first['units'][0]; receipt=self.home/'state/place-write/write'/(row['unit_id']+'.json')
        value=load(receipt); value['result']['answer']['fact']='drift'; save(receipt,value)
        with self.assertRaises(Stop): w.assert_completed(self.plan,self.home)
    def test_source_generated_plan_drift_block_before_db(self):
        self.run_phases(); gpath=self.home/'state/generated/R05.json'; original=load(gpath)
        changed=copy.deepcopy(original); changed['facts'][0]['fact']='누출'; save(gpath,changed)
        with self.assertRaises(Stop): w.assert_completed(self.plan,self.home)
        save(gpath,original)
        with self.assertRaises(Stop): w.assert_completed({**self.plan,'host':'other'},self.home)
        (self.home/'v1/record.md').write_text(self.text+'changed')
        with self.assertRaises(Stop): w.assert_completed(self.plan,self.home)
    def test_precompute_uses_actual_unit_domains_no_cross_product_no_old_fix(self):
        self.run_phases(); values,generated,_=precompute.inventory(self.plan,self.home)
        self.assertIn('R05',generated)
        self.assertTrue(any('주문관리' in t for t in values['passage']))
        self.assertTrue(any('검색' in t for t in values['passage']))
        self.assertFalse(any('unit-first ›' in t for t in values['passage']))
    def test_whole_document_grounding_not_only_cited_lines(self):
        self.run_phases()
        self.assertTrue(all(q[0]['source'].splitlines()==self.text.splitlines() for q in self.fake.questions if q[2] in (sg.SENTENCE_QUESTION,sg.NAMES_QUESTION)))
        self.assertIn('주문관리파트',sg.excluded_names({'domain':'주문관리','domain_aliases':['주문관리파트'],'subject':'예약 시트'}))
    def test_no_foreign_link_rows_even_when_supplied_and_own_leaves_preserved(self):
        cur=Mock(); cur.fetchone.return_value=('주문관리',)
        fact={'subject':'예약 시트','fact':'예약 시트는 환경설정의 포장단위를 따른다.', 'leaf_reference_mode':'own',
              'leaf_links':[{'ord':0,'subject_id':'foreign'}]}
        with patch('subject_dict.follow',side_effect=AssertionError('no foreign follow')):
            pg._write_form(cur,'h','f',1,fact,'own')
        inserts=[c.args[1] for c in cur.execute.call_args_list if 'insert into knowledge.fragment_leaf' in c.args[0]]
        self.assertTrue(inserts); self.assertTrue(all(row[5] in ('own',None) for row in inserts))
    def test_phase_order_and_uncertain_choice_fail_closed(self):
        with self.assertRaises(Stop): w.run('place',self.plan,self.home,self.paid,self.fake.ports())
        unit={'content':'주문관리파트의 일정표','source_ranges':[[1,1]],'target_expression':'일정표'}
        ports=w.Ports(self.fake.model,self.fake.encode,lambda *args:{'c0':.5,'none':.5},self.fake.ask)
        with self.assertRaisesRegex(ValueError,'source concept'):
            w.place_identity('domain',unit,None,[{'name':'주문관리','description':'예약'}],ports,5)
    def test_server_record_preserves_fixed_subject_and_disables_leaf_hints(self):
        import knowledge_cli as cli, subject_dict as sd
        from test_pilot_repairs_offline import Cursor
        row=self.run_phases()['units'][0]
        def resolve(name,sentence):
            resolve.provisional={'status':'PROVISIONAL','as_written':name}
            return name,'exact'
        with patch.object(pg,'connect',return_value=Cursor()),patch.object(sd,'resolver',side_effect=AssertionError('no post-selection resolver')), \
             patch('identity_ref.resolve',return_value={'subject_id':row['fact']['subject_id'],'name':row['fact']['subject']}), \
             patch.object(pg,'register',return_value={'entry_id':'entry'}) as register, \
             patch.object(cli,'snapshot',return_value={'baseline':{}}),patch.object(cli,'baseline_ref',return_value=None):
            result=a.record_unit(row['unit_id'],row['fact'],lambda data,**kw:cli.record('fake',data,**kw),'fake-host',lambda *args:[])
        self.assertEqual(result['entry_id'],'entry')
        stored=register.call_args.args[1]['facts'][0]
        self.assertEqual(stored['subject'],'예약 시트'); self.assertEqual(stored['leaf_reference_mode'],'own')
        self.assertEqual(stored['leaf_provisional'],[])
    def test_server_record_refuses_post_write_subject_rename(self):
        import knowledge_cli as cli, subject_dict as sd
        from test_pilot_repairs_offline import Cursor
        row=self.run_phases()['units'][0]
        def resolve(name,sentence):
            resolve.provisional={'status':'PROVISIONAL','as_written':name}
            return '다른 이름','exact'
        with patch.object(pg,'connect',return_value=Cursor()),patch.object(sd,'resolver',side_effect=AssertionError('no resolver')), \
             patch('identity_ref.resolve',return_value={'subject_id':row['fact']['subject_id'],'name':'다른 이름'}), \
             patch.object(pg,'register') as register:
            result=a.record_unit(row['unit_id'],row['fact'],lambda data,**kw:cli.record('fake',data,**kw),'fake-host',lambda *args:[])
        self.assertFalse(register.called); self.assertEqual(result['held_facts'][0]['errors'][0]['code'],'E_IDENTITY')

    def test_paid_journal_response_reuse_and_uncertain_no_replay(self):
        from safety import Ledger
        ledger=Ledger(self.home/'journal.jsonl','binding',{'absorption':1.})
        effect=Mock(return_value={'cost':.01})
        ledger.call('absorption','unit',.02,effect,lambda r:r['cost'])
        resumed=Ledger(ledger.path,'binding',ledger.caps)
        resumed.call('absorption','unit',.02,effect,lambda r:r['cost'])
        self.assertEqual(effect.call_count,1)
        with self.assertRaises(Stop): resumed.call('absorption','uncertain',.02,lambda:1/0)
        with self.assertRaises(Stop): resumed.call('absorption','new',.02,effect)


class DomainDocumentContext(unittest.TestCase):
    def fixture(self,none=.90):
        source='Header: caller-owned context\nContext retrieval recovers host/project context.\nOther context is not another target.'
        unit={'content':source.splitlines()[1],'target_expression':'host/project context','source_ranges':[[2,2]],
              'host_id':'context-fixture','unit_id':'00000000-0000-4000-8000-000000000001'}
        catalog=[{'name':'하네스','description':'문서 탐색','aliases':[]},{'name':'인프라','description':'CI 환경','aliases':[]}]
        seen=[];named=[]
        def choose(kind,packet,options):
            seen.append(('choose',kind,copy.deepcopy(packet),copy.deepcopy(options)))
            final=all(k=='none' or k.isdigit() for k in options)
            n=none if final else .54
            return {k:n if k=='none' else (1-n)/(len(options)-1) for k in options}
        def ask(state,labels,question):
            seen.append(('ask',question,copy.deepcopy(state),list(labels)))
            return [.99 if question==w.DDD2 else .64 if question==w.DOMAIN_QUESTION else .23 if question==w.DOMAIN_COVERED_QUESTION else .21]
        def worker(role,packet):
            named.append((role,copy.deepcopy(packet)))
            return {'original_expression':'host/project context','source_concept':'host/project context',
                    'name':'맥락관리','description':'host/project 맥락을 회복하고 활용하는 업무 영역'}
        ports=w.Ports(lambda *a:self.fail('legacy model used'),lambda texts:[[1.,0.] for _ in texts],choose,ask,worker=worker)
        return source,unit,catalog,ports,seen,named
    def test_all_domain_predicates_share_raw_document_and_keep_inputs(self):
        source,unit,catalog,ports,seen,named=self.fixture();before=copy.deepcopy((unit,catalog));callbacks=(ports.choose,ports.ask)
        result=w.place_identity('domain',unit,None,catalog,ports,5,document=source)
        self.assertTrue(result['new']);self.assertEqual(result['chosen']['name'],'맥락관리')
        self.assertEqual((unit,catalog),before);self.assertEqual((ports.choose,ports.ask),callbacks)
        self.assertEqual(len([r for r in seen if r[0]=='choose']),3)
        questions={r[1] for r in seen if r[0]=='ask'}
        self.assertTrue({w.DOMAIN_QUESTION,w.DOMAIN_SAME_QUESTION,w.DOMAIN_COVERED_QUESTION}<=questions)
        for _,_,state,_ in seen:
            self.assertEqual(state['source'],source);self.assertEqual(state['source_start_line'],1)
            self.assertEqual(state['unit'],unit)
        self.assertEqual(len(named),1);self.assertEqual(set(named[0][1]),{'source','unit','domain','existing'})
        self.assertEqual(named[0][1]['source'],source)
    def test_actual_058_none_remains_held_after_valid_candidate(self):
        source,unit,catalog,ports,seen,named=self.fixture(.58)
        with self.assertRaisesRegex(w.PlacementHeld,'requires explicit Jev none'):
            w.place_identity('domain',unit,None,catalog,ports,5,document=source)
        self.assertEqual(len(named),1);self.assertEqual(len([r for r in seen if r[0]=='choose']),3)
    def test_no_document_is_legacy_unit_only_not_fabricated_full_source(self):
        source,unit,catalog,ports,seen,named=self.fixture()
        w.place_identity('domain',unit,None,catalog,ports,5)
        self.assertTrue(seen);self.assertTrue(all('source' not in r[2] for r in seen))
    def test_exact_match_needs_no_new_judgement_or_name(self):
        source,unit,catalog,ports,seen,named=self.fixture();unit['target_expression']='하네스'
        result=w.place_identity('domain',unit,None,catalog,ports,5,document=source)
        self.assertFalse(result['new']);self.assertEqual(result['chosen']['name'],'하네스')
        self.assertFalse(seen);self.assertFalse(named)
    def test_subject_path_does_not_receive_domain_context(self):
        source,unit,_,ports,seen,_=self.fixture()
        result=w.place_identity('subject',unit,{'name':'기억','description':'기억 업무'},[],ports,5,document=source)
        self.assertTrue(result['new']);self.assertTrue(seen)
        self.assertTrue(all('source' not in r[2] for r in seen))
    def test_original_ports_remain_unwrapped_after_domain(self):
        source,unit,catalog,ports,seen,_=self.fixture()
        w.place_identity('domain',unit,None,catalog,ports,5,document=source)
        ports.ask({'unit':unit},['candidate'],w.DOMAIN_QUESTION)
        self.assertNotIn('source',seen[-1][2])
    def test_context_is_code_owned_and_callback_mutations_do_not_touch_inputs(self):
        source,unit,_,ports,_,_=self.fixture();packet={'unit':unit,'source':'FORGED','source_start_line':99};options={'none':'none'}
        before=copy.deepcopy((packet,options))
        def mutate(kind,state,criteria):
            self.assertEqual(state['source'],source);self.assertEqual(state['source_start_line'],1)
            state['unit']['content']='MUTATED';criteria['none']='MUTATED';return {'none':1.}
        from dataclasses import replace
        wrapped=w._domain_context_ports(replace(ports,choose=mutate),source)
        wrapped.choose('domain',packet,options)
        self.assertEqual((packet,options),before)
    def test_structural_ports_preserve_extra_fields_and_original_callbacks(self):
        from types import SimpleNamespace
        source,unit,catalog,ports,seen,_=self.fixture()
        marker=object();structural=SimpleNamespace(**vars(ports),owner_marker=marker)
        callbacks=(structural.choose,structural.ask)
        scoped=w._domain_context_ports(structural,source)
        self.assertIs(scoped.owner_marker,marker);self.assertIsNot(scoped,structural)
        result=w.place_identity('domain',unit,None,catalog,structural,5,document=source)
        self.assertTrue(result['new']);self.assertEqual((structural.choose,structural.ask),callbacks)
        self.assertTrue(all(row[2]['source']==source for row in seen))
    def test_invalid_supplied_document_stops_before_any_effect(self):
        for document in ('','   ',123,[]):
            source,unit,catalog,ports,seen,named=self.fixture()
            with self.subTest(document=document),self.assertRaisesRegex(w.PlacementHeld,'nonempty document'):
                w.place_identity('domain',unit,None,catalog,ports,5,document=document)
            self.assertFalse(seen);self.assertFalse(named)


if __name__=='__main__': unittest.main()
