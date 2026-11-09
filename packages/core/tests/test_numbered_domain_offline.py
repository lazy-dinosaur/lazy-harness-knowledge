"""G16 numbered-source/domain/cash/automatic-recovery protection, strictly offline."""
import test_offline
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import place_write as w
import pipeline as p
import run_paid_pilot as pilot
from safety import Ledger, Stop, save, digest
from ddd_criterion import criterion_sha, CALIBRATED_SHA256
import test_place_write_offline as previous

class NumberedDomain(unittest.TestCase):
    def fixture(self):
        case=previous.UnitFirst();case.setUp();self.addCleanup(case.doCleanups);return case
    def test_all_luna_sources_numbered_raw_grounding_unchanged(self):
        case=self.fixture()
        # Task377 stage-0 repair: production no longer has a legacy text-model path (ports.model refuses);
        # Luna roles go through the controller worker, whose role_data numbers the source (N: line).
        import active_worker as aw
        with patch('active_judgements.make_state_choice'),patch('active_judgements.make_ask'):
            ports=w.production(Mock(),{},writer_factory=lambda paid:Mock(),worker_factory=lambda paid:Mock())
        with self.assertRaises(aw.WorkerFailure):ports.model('judge',{'source':case.text},w.WRITE_TASK)
        n=len(case.text.splitlines())
        data=aw.role_data('extract',{'source':case.text,'source_start':1,'chunk_range':[1,n],'context':{'document_title':'t','heading_path':[]},'origin':'document','migration':False})
        self.assertEqual(data['source'],'\n'.join(f'{i}: {line}' for i,line in enumerate(case.text.splitlines(),1)));self.assertEqual(data['source_raw'],case.text)
        self.assertTrue(all(not r['held_reasons'] for r in case.run_phases()['units']))
        self.assertEqual(criterion_sha(),CALIBRATED_SHA256)
    def test_quotes_code_sliced_not_paraphrased(self):
        case=self.fixture();row=case.run_phases()['units'][0]
        answer={**row['written'],'fact':'예약 시트는 `MappedIdentity`의 시간을 사용한다.','quote':'fake','evidence_quote':'fake','claim_quote':'fake'}
        answer['_source_modality']=case.modality(row,answer['fact'],answer.get('kind','fact'))  # Task377 stage-0 repair
        fact=w.validate_fact(case.r,case.home,row,row,answer)
        self.assertEqual(fact['evidence_quote'],case.text.splitlines()[0]);self.assertEqual(fact['evidence_refs'][0]['quote'],case.text.splitlines()[0])
        self.assertNotIn('quote',fact);self.assertNotIn('claim_quote',fact)
        rejected=p.checked_facts(case.r,{'partition_key':'예약','facts':[dict(answer,subject='예약 시트',subject_description='설명',aliases=[])]},reuse_existing=False,home=case.home)
        self.assertIn('E_CLAIM_QUOTE',str(rejected))
    def test_only_trivial_eof_range_clamped(self):
        text='one\ntwo\n'
        self.assertEqual(w.source_ranges([1,2],text),[[1,2]]);self.assertEqual(w.source_ranges([[2,3]],text),[[2,2]])
        for value in ([[3,3]],[[0,2]],[[1,4]],[[True,2]],[[2,1]]):
            with self.subTest(value=value),self.assertRaises(w.SourceRangeError):w.source_ranges(value,text)
    def test_modal_like_new_domains_rejected(self):
        for name in ('모달','확인모달','StockGrid','parcel.create','stock.py'):
            unit={'host_id':'fake-host','unit_id':'30000000-0000-4000-8000-000000000003','content':name+'는 열리고 닫힌다.','target_expression':name}
            model=Mock(return_value={'original_expression':name,'source_concept':name,'name':name,'description':'화면 대상'});ask=Mock(return_value=[.01])
            ports=w.Ports(model,lambda xs:[[0.,0.]]*len(xs),lambda k,p,o:{x:.99 if x=='none' else .01 for x in o},ask)
            with self.subTest(name=name),self.assertRaisesRegex(w.PlacementHeld,'business area'):w.place_identity('domain',unit,None,[],ports,5,document=unit['content'])
            self.assertIn('화면·모달·컴포넌트·API·도구·파일',ask.call_args.args[2]);self.assertIn(w.DOMAIN_CRITERION,model.call_args.args[2]);self.assertNotIn('{i}',model.call_args.args[2])
    def test_ambiguity_same_boundary_or_create_both_kinds(self):
        for kind in ('domain','subject'):
            for probability in (.5,.499):
                unit={'host_id':'fake-host','unit_id':'30000000-0000-4000-8000-000000000003','content':'재고표는 수량을 표시한다.','target_expression':'재고표'};catalog=[{'subject_id':'10000000-0000-4000-8000-000000000001','name':'StockGrid','description':'재고 표 대상','aliases':[]}]
                # Task377 stage-0 repair: a domain's ambiguous top now asks DOMAIN_MEMBERSHIP_QUESTION (candidate+unit) instead of same-meaning.
                def ask(state,labels,question):
                    if question in (w.DDD2,w.DOMAIN_QUESTION):return [.99]
                    if question==w.DOMAIN_MEMBERSHIP_QUESTION:return [probability]
                    if question==w.DOMAIN_OVERLAP_QUESTION:return [.01]  # Task382
                    return [probability if state.get('proposed',{}).get('name')=='재고표' else .01]
                model=Mock(return_value={'original_expression':'재고표','source_concept':'재고표','name':'새대상','description':'재고 대상'})
                def choose(k,p,o):
                    # The all-catalog confirmation requires explicit none for a new domain.
                    return {x:(.99 if x=='none' else .001) if '0' in o else .4 for x in o}
                ports=w.Ports(model,lambda xs:[[0.,0.]]*len(xs),choose,ask)
                result=w.place_identity(kind,unit,{'name':'재고'},catalog,ports,5,document=unit['content'])
                self.assertEqual(result['new'],probability<.5);self.assertEqual('재고표' in result['chosen']['aliases'],kind=='subject' and probability<.5);self.assertEqual(result['ambiguous_domain_membership' if kind=='domain' else 'ambiguous_same_meaning']['score'],probability)
                if probability<.5:self.assertEqual(len(result['same_meaning_checks']),1)
    def test_cash_cap_both_providers_and_released(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(Path(tmp)/'paid.jsonl','b',{'absorption':1,'embedding':1},cash_limit=.5)
            ledger.append({'id':'old','step':'absorption','state':'released','account':'cash','charge':.3});ledger.call('embedding','first',.1,lambda:{},lambda _:.1)
            with self.assertRaisesRegex(Stop,'total cash'):ledger.call('absorption','second',.101,lambda:self.fail('effect forbidden'))
            self.assertAlmostEqual(sum(ledger.spent(s,'cash') for s in ledger.caps),.4)
    @unittest.skip('needs experiment inputs (restore report, host records) not shipped here; moves to packages/eval with a synthetic fixture')
    def test_restart_freezes_prior_cash_and_preserves_total_cap(self):
        import os, prepare
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            home=Path(tmp)/'pilot-area-10'
            with patch.object(prepare,'scope_home',return_value=home),patch.dict(os.environ,{'LH_PILOT_CASH_PRIOR_USD':'0.020433574'}):
                prepare.prepare('pilot-area-10')
            from safety import load
            plan=load(home/'plan.json')
            self.assertEqual(plan['cash_caps']['total_usd'],.5)
            available=plan['cash_caps']['total_usd']-plan['cash_caps']['prior_spent_usd']
            ledger=Ledger(home/'state/paid.jsonl',digest(plan),plan['caps'],cash_limit=available)
            with self.assertRaisesRegex(Stop,'total cash'):ledger.admit_cash(.48)
            with patch.object(prepare,'scope_home',return_value=home),patch.dict(os.environ,{'LH_PILOT_CASH_PRIOR_USD':'-1'}):
                with self.assertRaisesRegex(ValueError,'prior pilot cash'):prepare.prepare('pilot-area-10')

    def test_readback_invocation_and_retry_limits(self):
        self.assertIn('import pipeline; pipeline.main()',pilot.invocation('absorb','sha')[3])
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);plan={'caps':{'absorption':1}};save(home/'plan.json',plan);ledger=Ledger(home/'state/paid.jsonl',digest(plan),plan['caps'])
            ledger.append({'id':'r','step':'absorption','state':'reserved','account':'cash','charge':.002});save(home/'state/errors/r.json',{'status':503})
            self.assertEqual(pilot.recover(home,'',0),'r')
            self.assertEqual(pilot.recover(home,'',4),'r')
            with self.assertRaises(RuntimeError):pilot.recover(home,'',5)
            save(home/'state/errors/r.json',{'status':499,'body':'internal_error'})
            self.assertEqual(pilot.recover(home,'',0),'r')
            save(home/'state/errors/r.json',{'status':400})
            with self.assertRaises(RuntimeError):pilot.recover(home,'',0)
            save(home/'state/errors/r.json',{'status':429});ledger.append({'id':'r','step':'absorption','state':'released','account':'cash','charge':.002});ledger.append({'id':'r','step':'absorption','state':'reserved','account':'cash','charge':.002})
            with self.assertRaises(RuntimeError):pilot.recover(home,'',1)

if __name__=='__main__':unittest.main()
