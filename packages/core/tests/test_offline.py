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

import offline_guard  # noqa: F401  (installs the guards before production imports)
from offline_guard import forbidden
import safety
from safety import Ledger, Paid, Stop, digest, load, save
import generation

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





def loadlines(path):return [json.loads(x) for x in path.read_text().splitlines()]
if __name__=='__main__':unittest.main()
