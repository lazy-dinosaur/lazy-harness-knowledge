"""Experiment-runner cases split out of packages/core/tests/test_pilot_repairs_offline.py.py (issue #6 step 7)."""
import offline_guard  # noqa: F401
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import pipeline as p
import generation
import knowledge_cli as cli
import store_pg as pg
import subject_dict as sd
import subject_contract as sc
import subject_shorten as shorten
from area_absorb import assigned_area, project_holds
from area_report import acceptance
from safety import save, load, digest, Stop, Ledger
import test_pilot_repairs_offline as _core
import unittest as _ut
globals().update({k: v for k, v in vars(_core).items() if not k.startswith('__') and not (isinstance(v, type) and issubclass(v, _ut.TestCase))})


class PilotRepairsEval(unittest.TestCase):
    # setUp/tearDown/helpers come from the core case; only the experiment-runner tests live here
    locals().update({k: v for k, v in vars(_core.PilotRepairs).items() if not k.startswith("test") and not k.startswith("__")})

    @unittest.expectedFailure  # known failure carried from the source folder (KeyError 'judgement_body'); tracked in issue #1 P0
    def test_bad_fact_held_good_recorded_original_index_and_report(self):
        bad=fact('채팅방 캐시 overlay 권한 정책')
        out,register=self.record([fact(), bad, fact('시트 확대')])
        self.assertEqual(out['recorded_indices'],[0,2],out)
        self.assertEqual(out['held_facts'][0]['fact_index'],1)
        body=register.call_args.args[1]
        self.assertEqual(len(body['facts']),2)
        self.assertEqual(body['area_validation']['held_facts'],out['held_facts'])
        row={'record_id':'R03','path':'spec/sheet.md','expected_facts':3,'vector_or_structure_missing':0,
             'facts':[{'fact_index':0,'decision':'absorbed'}, {'fact_index':1,'decision':'absorbed'}], 'status':'held'}
        project_holds(row,out)
        self.assertEqual(row['status'],'absorbed_except_held')
        self.assertEqual(row['facts'][1]['source_fact_index'],2)
        self.assertEqual(row['disposition'],'document absorbed except 1 held facts')
        report=acceptance({'records':[row]}, {})
        self.assertEqual(report['held_fact_count'],1)
        self.assertEqual(report['held_facts'][0]['errors'][0]['code'],'E_SUBJECT_NAME_LENGTH')
        self.assertFalse(report['complete'])

    def test_parent_example_leak_grounding_and_existing_reuse(self):
        f=fact('문서 검색', parent_subject={'name':'AI상담','subject_type':'기능','description':'문서 검색 상위'})
        source='Map-First Retrieval Vocabulary. 문서 검색은 기록 지도를 읽는다.'
        errors=sc.grounding_errors(f,source)
        self.assertEqual([e['code'] for e in errors],['E_SUBJECT_UNGROUNDED'])
        self.assertEqual(errors[0]['where'],'parent_subject')
        self.assertTrue(sc.grounding_errors(f,source,['AI상담']))  # existing dictionary is not source evidence
        out,register=self.record([f], source_text=source)
        register.assert_not_called()
        row={'record_id':'R05','path':'domain/purpose-scoped-retrieval.md','expected_facts':1,
             'vector_or_structure_missing':0,'facts':[],'status':'held'}
        project_holds(row,out)
        self.assertEqual(len(acceptance({'records':[row]}, {})['ungrounded_rejections']),1)
        # Declared aliases alone cannot launder an unrelated new name.
        self.assertTrue(sc.grounding_errors({'subject':'AI상담','fact':'기록 지도를 읽는다','aliases':['기록 지도']}))

    def test_absorb_continues_after_one_held_fact(self):
        import area_absorb, area_catalog, embed, worktime_driver as wd, digest_driver as dd
        record={'record_id':'R03','sha256':'sha','path':'a.md'}
        plan={'records':[record],'area_catalog':True,'area_fact_isolation':True}
        g={'status':'lint-valid','partition_key':'old hint','facts':[fact(),fact('채팅방 캐시 overlay 권한 정책')]}
        save(self.home/'state/generated/R03.json',g)
        (self.home/'v1').mkdir();(self.home/'v1/a.md').write_text('예약 시트를 확대한다')
        result={'entry_id':'entry','recorded_indices':[0],'held_facts':[{'fact_index':1,'fact':g['facts'][1],
                 'errors':[{'code':'E_SUBJECT_NAME_LENGTH','where':'subject','detail':'too long'}]}]}
        class AbsorbCursor(Cursor):
            def cursor(self): return self
            def fetchone(self): return ('active',)
        adapters={'same':Mock(),'embed_fn':Mock(),'choose':Mock(),'confirm':Mock()}
        with patch.object(p,'HERE',self.home),patch.object(p,'source_home',return_value=self.home), \
             patch.object(generation,'adapters',return_value=(adapters,{})),patch.object(p,'adapters',return_value=(adapters,{})),patch.object(area_catalog,'install'), \
             patch.object(area_absorb,'assigned_area',return_value='예약'),patch.object(pg,'connect',return_value=AbsorbCursor()), \
             patch.object(cli,'record',return_value=result) as record_call,patch.object(wd,'run_worktime') as work, \
             patch.object(pg,'complete'),patch.object(dd,'run_digestion') as digest_call, \
             patch.object(generation,'maintain',return_value={}),patch.object(p,'maintain',return_value={}),patch.object(embed.CACHE,'stats',return_value={'calls':0}):
            p.absorb(plan,'fake',Mock(),{})
        self.assertTrue(record_call.call_args.kwargs['isolate_invalid'])
        self.assertEqual(record_call.call_args.kwargs['assigned_area'],'예약')
        self.assertEqual(record_call.call_args.args[1]['facts'][0]['domain'],'예약')
        work.assert_called_once();digest_call.assert_called_once()
        self.assertNotIn('name_area',digest_call.call_args.kwargs)
        self.assertEqual(load(self.home/'state/fact-dispositions/R03.json')['held_facts'],result['held_facts'])


if __name__ == '__main__':
    unittest.main()
