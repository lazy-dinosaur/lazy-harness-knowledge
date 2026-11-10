"""Pi adapter cases split out of packages/core/tests/test_active_worker_offline.py (issue #14 step 2): they drive
active_worker_transport.make_worker with injected fake transports."""
import offline_guard as test_offline
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
import active_worker_transport as t
import active_judgements as j
import source_grounding as sg
import digest_driver as dd
import store_pg
from safety import Ledger, Stop, load, save, digest
from model_output import ModelOutputInvalid
from writer_worker import build_request as writer_request
import test_place_write_offline as previous
import test_active_worker_offline as _core
import unittest as _ut
globals().update({k: v for k, v in vars(_core).items() if not k.startswith("__") and not (isinstance(v, type) and issubclass(v, _ut.TestCase))})


class ActiveContractsPi(unittest.TestCase):
    locals().update({k: v for k, v in vars(_core.ActiveContracts).items() if not k.startswith("test") and not k.startswith("__")})

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


class NamingProposalContractPi(unittest.TestCase):
    locals().update({k: v for k, v in vars(_core.NamingProposalContract).items() if not k.startswith("test") and not k.startswith("__")})

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


if __name__ == '__main__':
    unittest.main()
