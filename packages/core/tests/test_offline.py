"""Standalone no DB/network/process tests. Run python3 scale-04/test_offline.py."""
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

# Install before production imports; no pytest/conftest or model services.
os.environ['LH_EMBED_PROVIDER']='local'
os.environ['LH_KNOWLEDGE_CONFIG']=str(Path(__file__).parent/'offline-config-does-not-exist.json')
def forbidden(*a,**k): raise AssertionError('offline side effect forbidden')
socket.create_connection=forbidden
socket.getaddrinfo=forbidden
socket.socket.connect=forbidden
socket.socket.connect_ex=forbidden
subprocess.run=forbidden
subprocess.Popen=forbidden
# Import-only DB driver stub: these standalone tests inject fake connections.
# Missing optional psycopg must never require installation or allow a real DB.
import sys
import types
class OfflineDriverError(Exception): pass
_offline_driver = types.ModuleType('psycopg')
_offline_driver.Error = OfflineDriverError
_offline_driver.connect = forbidden
sys.modules['psycopg'] = _offline_driver
import safety
from safety import Ledger, Paid, Stop, digest, load, save
import pipeline as p
import generation
import prepare

class Offline(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.ledger=Ledger(self.root/'paid.jsonl','bound',{'test':1,'embedding':1})
    def test_reservation_before_fake_judge_and_resume(self):
        def judge():
            self.assertEqual(loadlines(self.ledger.path)[0]['state'],'reserved')
            return {'answers':{'yes':.9},'cost':.1}
        one=self.ledger.call('test','k',.2,judge,lambda x:x['cost'])
        resumed=Ledger(self.ledger.path,'bound',{'test':1})
        self.assertEqual(resumed.call('test','k',.2,forbidden),one)
        self.assertAlmostEqual(resumed.spent('test'),.1)
    def test_cap_before_effect(self):
        with self.assertRaises(Stop): self.ledger.call('test','x',1.01,forbidden)
        self.assertFalse(self.ledger.path.exists())
    def test_uncertain_not_replayed_even_with_changed_key(self):
        with self.assertRaises(Stop): self.ledger.call('test','x',.2,lambda:1/0)
        resumed=Ledger(self.ledger.path,'bound',{'test':1})
        for key in ('x','new-prompt'):
            with self.assertRaises(Stop): resumed.call('test',key,.2,forbidden)
        self.assertEqual(resumed.spent('test'),.2)
    def test_binding_and_result_tamper(self):
        self.ledger.call('test','x',.2,lambda:{'a':1})
        with self.assertRaises(Stop): Ledger(self.ledger.path,'changed',{'test':1})
        row=self.ledger.rows[-1]; save(self.root/row['result'],{'a':2})
        with self.assertRaises(Stop): self.ledger.call('test','x',.2,forbidden)
    def test_actual_overrun_stops_and_is_charged(self):
        with self.assertRaises(Stop): self.ledger.call('test','x',.2,lambda:{'cost':1.1},lambda x:x['cost'])
        self.assertEqual(self.ledger.spent('test'),1.1)
        with self.assertRaises(Stop): self.ledger.call('test','y',.1,forbidden)
    def test_all_turn_cost_tokens(self):
        events=[]
        for cost in (.01,.02):
            events.append(json.dumps({'type':'turn_end','message':{'role':'assistant','content':[{'type':'text','text':'answer'}],
                         'usage':{'input':10,'output':20,'cacheRead':5,'cost':{'total':cost}}}}))
        fake=type('Result',(),{'stdout':'\n'.join(events),'returncode':0})()
        paid=Paid(self.ledger,'test',{})
        with patch.object(safety.subprocess,'run',return_value=fake):
            text,usage=paid.model('read input.json',self.root)
        self.assertEqual(text,'answer');self.assertAlmostEqual(usage['usage']['cost_usd'],.03)
        response=load(self.root/self.ledger.rows[-1]['result'])
        self.assertEqual(len(response['turns']),2)
    def test_http_unknown_cost_reservation_and_no_retry(self):
        import urllib.request
        paid=Paid(self.ledger,'test',{'jev_base_url':'https://openrouter.ai/api'})
        paid.original_open=lambda *a,**k:io.BytesIO(b'{"answers":{"a":{"noul":0.9}}}')
        request=urllib.request.Request('https://openrouter.ai/api/v1/systemone',data=b'{"questions":{"a":{}}}')
        self.assertEqual(json.load(paid.http(None,request))['answers']['a']['noul'],.9)
        self.assertEqual(self.ledger.spent('test'),.002)
        with self.assertRaises(Stop): paid.http(None,'https://remote-db.example/')
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
    @unittest.skip('needs experiment inputs (restore report, baselines) not shipped here; moves to packages/eval with a synthetic fixture')
    def test_corrected_baselines_and_exact_inputs(self):
        records,qs=prepare.inputs();base=prepare.baseline(qs)
        self.assertEqual(len(records),598);self.assertEqual(len(qs),64)
        self.assertFalse(prepare.EXCLUDED & {q['qid'] for q in qs})
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

def loadlines(path):return [json.loads(x) for x in path.read_text().splitlines()]
if __name__=='__main__':unittest.main()
