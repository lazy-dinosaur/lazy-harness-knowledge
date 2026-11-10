"""G16 numbered-source/domain/cash/automatic-recovery protection, strictly offline."""
import offline_guard as test_offline  # noqa: F401  socket/process guards installed BEFORE application imports
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import place_write as w
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
    def test_only_trivial_eof_range_clamped(self):
        text='one\ntwo\n'
        self.assertEqual(w.source_ranges([1,2],text),[[1,2]]);self.assertEqual(w.source_ranges([[2,3]],text),[[2,2]])
        for value in ([[3,3]],[[0,2]],[[1,4]],[[True,2]],[[2,1]]):
            with self.subTest(value=value),self.assertRaises(w.SourceRangeError):w.source_ranges(value,text)
    def test_cash_cap_both_providers_and_released(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(Path(tmp)/'paid.jsonl','b',{'absorption':1,'embedding':1},cash_limit=.5)
            ledger.append({'id':'old','step':'absorption','state':'released','account':'cash','charge':.3});ledger.call('embedding','first',.1,lambda:{},lambda _:.1)
            with self.assertRaisesRegex(Stop,'total cash'):ledger.call('absorption','second',.101,lambda:self.fail('effect forbidden'))
            self.assertAlmostEqual(sum(ledger.spent(s,'cash') for s in ledger.caps),.4)


if __name__=='__main__':unittest.main()
