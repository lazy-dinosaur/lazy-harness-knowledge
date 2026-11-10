"""Experiment-runner cases split out of packages/core/tests/test_offline.py.py (issue #6 step 7)."""
import offline_guard  # noqa: F401
from eval_fixtures import synthetic_inputs
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import safety
from safety import Ledger, Paid, Stop, digest, load, save
import pipeline as p
import generation
import prepare
import test_offline as _core
import unittest as _ut
globals().update({k: v for k, v in vars(_core).items() if not k.startswith('__') and not (isinstance(v, type) and issubclass(v, _ut.TestCase))})


class OfflineEval(unittest.TestCase):
    # setUp/tearDown/helpers come from the core case; only the experiment-runner tests live here
    locals().update({k: v for k, v in vars(_core.Offline).items() if not k.startswith("test") and not k.startswith("__")})

    def test_disposition_is_not_generation_completion(self):
        self.assertEqual(p.disposition(0,[]),'failed')
        self.assertEqual(p.disposition(2,['absorbed']),'held')
        self.assertEqual(p.disposition(1,['absorbed'],1),'held')
        self.assertEqual(p.disposition(1,['rejected']),'failed')
        self.assertEqual(p.disposition(2,['absorbed','retained_as_evidence']),'absorbed')

    def test_readback_source_denominator_fake_sql(self):
        import store_pg
        class Cursor:
            description=[]
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def execute(self,sql,params):
                if 'jsonb_array_length' in sql:self.rows=[('entry',2,'absorbed')]
                elif 'distinct on' in sql:
                    self.description=[(x,) for x in ('entry_id','fact_index','decision','id','domain','subject_id','active','form','vector_ready')]
                    self.rows=[('entry',0,'absorbed','f','D','s',True,{'then':['x']},True)]
                else:self.rows=[]
            def fetchall(self):return self.rows
        class Conn:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def cursor(self):return Cursor()
        with patch.object(p,'HERE',self.root),patch.object(generation,'HERE',self.root),patch.object(store_pg,'connect',return_value=Conn()),patch.object(store_pg,'review_list',return_value=[]):
            result=p.readback({'records':[{'record_id':'R1','path':'a','sha256':'x'}]},'fake')
        self.assertFalse(result['complete']);self.assertEqual(result['counts']['held'],1)
        self.assertEqual(result['records'][0]['expected_facts'],2)

    def test_operator_form_and_verbatim_evidence(self):
        path=self.root/'v1/a.md';path.parent.mkdir();path.write_text('예약은 가능하다.\n')
        record={'path':'a.md','sha256':'sha'}
        answer={'partition_key':'예약','facts':[{'kind':'fact','subject':'예약','subject_type':'기능','subject_description':'예약 가능 여부를 관리하는 기능','fact':'예약은 가능하다',
                'reason':'동작 보존','source_ranges':[[1,1]]}]}
        with patch.object(p,'HERE',self.root),patch.object(generation,'HERE',self.root):
            got=p.checked_facts(record,answer)
            self.assertEqual(got['facts'][0]['evidence_refs'][0]['quote'],'예약은 가능하다.')
            answer['facts'][0]['source_ranges']=[[0,2]]
            rejected=p.checked_facts(record,answer)
            self.assertEqual(rejected['facts'],[])
            self.assertEqual(len(rejected['rejected']),1)

    def test_corrected_baselines_and_exact_inputs(self):
        # synthetic neutral inputs; the host corpus manifest lives with the host's inputs outside this repository
        with synthetic_inputs():
            m=prepare.manifest();records,qs=prepare.inputs();base=prepare.baseline(qs)
        self.assertEqual(len(records),m['expected']['records']);self.assertEqual(len(qs),m['expected']['questions'])
        self.assertFalse(set(m['excluded_questions']) & {q['qid'] for q in qs})
        self.assertEqual({q['qid']:q['nuggets'] for q in qs},{'Q1':['S1:c1'],'Q2':['S2:c1']})  # nugget superseded by the last verdict file is dropped
        for arm in base.values():self.assertEqual(len(arm['runs']),3)

    def test_four_configs_single_writer_and_chunk_bound(self):
        self.assertEqual(len(p.ARMS),4)
        self.assertEqual(p.ARMS['both'],('1','subject'))
        self.assertEqual(list(map(len,p.chunks('a'*12001))),[6000,6000,1])

    def test_migration_sequence(self):
        from local_db import MIGRATIONS
        self.assertEqual(len(MIGRATIONS),23)
        self.assertEqual([x[:4] for x in MIGRATIONS],[f'{i:04}' for i in range(1,24)])
        self.assertEqual(MIGRATIONS[-1], '0023_validation_receipt')
        for name in MIGRATIONS:self.assertTrue((safety.RUNNER/'migrations'/(name+'.sql')).is_file())


if __name__ == '__main__':
    unittest.main()
