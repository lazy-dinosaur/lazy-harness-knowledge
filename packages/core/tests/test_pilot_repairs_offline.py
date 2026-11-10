"""Pilot R12/R03/example leak regressions: fake DB only, guard-first."""
import offline_guard as test_offline  # noqa: F401  socket/process guards installed BEFORE application imports
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import generation
import knowledge_cli as cli
import store_pg as pg
import subject_dict as sd
import subject_contract as sc
import subject_shorten as shorten
from area_absorb import assigned_area, project_holds
from safety import save, load, digest, Stop, Ledger


def fact(name='예약 시트 줌', **kw):
    return dict(subject=name, subject_type='기능', subject_description='시트 확대 기능',
                fact=name+'은 시트를 확대한다', reason='원문 기능 보존', kind='fact', operation='add',
                evidence_source='official_doc', evidence_refs=[{'type':'official_doc','locator':'spec/sheet.md#L1','quote':name+'은 시트를 확대한다'}], **kw)


class Cursor:
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def execute(self, q, args=None): self.q=q
    def fetchall(self): return []
    def fetchone(self): return (1,)
    def cursor(self): return Cursor()


class PilotRepairs(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)

    def record(self, facts, **options):
        def resolve(name, sentence):
            resolve.provisional={'status':'PROVISIONAL','as_written':name}
            return name,'new_unchecked'
        with patch.object(pg,'connect',return_value=Cursor()), patch.object(sd,'resolver',return_value=resolve), \
             patch.object(pg,'register',return_value={'entry_id':'entry'}) as register, \
             patch.object(cli,'snapshot',return_value={'baseline':{}}), patch.object(cli,'baseline_ref',return_value=None):
            out=cli.record('fake',{'host_id':'h','partition_key':'예약 시트 줌','facts':facts},
                           assigned_area='예약',isolate_invalid=True, **options)
        return out, register

    def test_r12_hint_is_not_area(self):
        f=fact(domain='예약 시트 줌', partition_key='예약 시트 줌', assigned_area='예약')
        self.assertFalse(sc.hierarchy_errors(f))
        out, register=self.record([f])
        self.assertIn('entry_id',out, out)
        self.assertEqual(register.call_args.args[1]['facts'][0]['domain'],'예약')

    def test_subject_and_parent_equal_assigned_area_rejected(self):
        self.assertIn('E_SUBJECT_AREA',{e['code'] for e in sc.hierarchy_errors(fact('예약',assigned_area='예약',partition_key='다른 힌트'))})
        f=fact(parent_subject={'name':'예약','subject_type':'기능','description':'영역 아님'}, assigned_area='예약')
        self.assertIn('E_SUBJECT_PARENT',{e['code'] for e in sc.hierarchy_errors(f)})
        self.assertTrue(sc.packed_name('예약의 시트', '예약'))
        self.assertFalse(sc.hierarchy_errors(fact(partition_key='예약 시트 줌')))


    def test_all_invalid_no_empty_register(self):
        out,register=self.record([fact('예약')])
        register.assert_not_called()
        self.assertEqual(len(out['held_facts']),1)


    def test_route_all_facts_once_and_resume_receipt(self):
        import domain_router as dr
        generated={'partition_key':'예약 시트 줌','facts':[fact(),fact('시트 확대')]}
        record={'record_id':'R12'}
        choose=Mock(return_value={'d0':.99,'none':.01})
        adapt={'choose':choose,'embed_fn':Mock(return_value=[[1.]]),'name_area':Mock()}
        with patch.object(pg,'connect',return_value=Cursor()), patch.object(dr,'load',return_value={'예약':{'status':'active','description':'일정 업무'}}), \
             patch.object(dr,'nearest',return_value=['예약']),patch.object(dr,'ensure'):
            self.assertEqual(assigned_area({},self.home,'fake','h',record,generated,adapt),'예약')
            self.assertEqual(assigned_area({},self.home,'fake','h',record,generated,adapt),'예약')
        choose.assert_called_once()
        self.assertIn('시트 확대',choose.call_args.args[0])
        adapt['name_area'].assert_not_called()

    def test_shorten_retry_once_success_and_replay(self):
        name='채팅방 캐시 overlay 권한 정책'
        g={'partition_key':name,'facts':[fact(name)]}
        answer={'items':[{'original':name,'name':'캐시 권한 정책','description':'시트 확대 기능'}]}
        with patch.object(generation,'packet_model',return_value=(answer,{})) as model:
            out, result=shorten.retry_once(self.home,'R03',g,Mock())
            again,_=shorten.retry_once(self.home,'R03',g,Mock())
        model.assert_called_once()
        self.assertEqual(out,again)
        self.assertFalse(result['failures'])
        self.assertEqual(out['facts'][0]['fact'],g['facts'][0]['fact'])
        self.assertEqual(out['facts'][0]['aliases'],[name])
        self.assertEqual(model.call_args.args[2]['objects'][0]['lint_errors'][0]['code'],'E_SUBJECT_NAME_LENGTH')
        self.assertEqual(model.call_args.kwargs['charge_step'],'generation')

    def test_retry_failure_persists_without_more_calls(self):
        g={'partition_key':'채팅','facts':[fact('채팅방 캐시 overlay 권한 정책')]}
        with patch.object(generation,'packet_model',return_value=({'items':[]},{})) as model:
            out,result=shorten.retry_once(self.home,'R03',g,Mock())
            shorten.retry_once(self.home,'R03',g,Mock())
        model.assert_called_once()
        self.assertEqual(out,g)
        self.assertTrue(result['failures'])
        with patch.object(generation,'packet_model',side_effect=AssertionError('no valid-name retry')):
            self.assertEqual(shorten.retry_once(self.home,'R12',{'facts':[fact()]},Mock())[1],{'failures':[]})


    def test_shorten_phase_retries_before_completion_once(self):
        from confirmed_area_catalog import write_confirmed
        plan={'scope':'pilot-area-10','area_catalog':True,'subject_shorten':True,'area_fact_isolation':True,
              'records':[{'record_id':'R03','sha256':'sha'}]}
        save(self.home/'state/plan.json',plan);write_confirmed(plan,self.home,digest(plan))
        long='채팅방 캐시 overlay 권한 정책'
        g={'source_sha':'sha','partition_key':'채팅','facts':[fact(long)]}
        save(self.home/'state/generated/R03.json',g)
        answer={'items':[{'original':long,'name':'캐시 권한 정책','description':'시트 확대 기능'}]}
        with patch.object(generation,'packet_model',side_effect=[({'items':[]},{}),(answer,{})]) as model:
            report=shorten.run(plan,self.home,Mock())
            again=shorten.run(plan,self.home,Mock())
        self.assertEqual(model.call_count,2)  # initial attempt + exactly one retry
        self.assertEqual(report,again);self.assertFalse(report['failures'])
        shorten.assert_completed(plan,self.home)
        self.assertEqual(load(self.home/'state/generated/R03.json')['facts'][0]['fact'],g['facts'][0]['fact'])


    def test_cached_shorten_packet_reused_only_on_exact_key(self):
        from confirmed_area_catalog import write_confirmed
        plan={'scope':'pilot-area-10','area_catalog':True,'subject_shorten':True,
              'records':[{'record_id':'R03','sha256':'sha'}]}
        save(self.home/'state/plan.json',plan);write_confirmed(plan,self.home,digest(plan))
        long='채팅방 캐시 overlay 권한 정책'; f=fact(long)
        g={'source_sha':'sha','partition_key':'채팅','facts':[f]}
        save(self.home/'state/generated/R03.json',g)
        packet={'area_hint':'채팅','objects':list(shorten.objects(f)),'kept_names':[],'known_mappings':{}}
        answer={'items':[{'original':long,'name':'캐시 권한 정책','description':'시트 확대 기능'}]}
        receipt={'input_sha':digest(packet),'answer':answer,'usage':{}}
        save(self.home/'state/subject-shorten-cache'/('R03-'+digest(packet)+'.json'),receipt)
        with patch.object(generation,'packet_model',side_effect=AssertionError('exact receipt must be reused')):
            report=shorten.run(plan,self.home,Mock())
        self.assertFalse(report['failures'])
        self.assertEqual(load(self.home/'state/generated/R03.json')['facts'][0]['subject'],'캐시 권한 정책')

    def test_receipt_cache_exact_key_and_source_drift(self):
        from reuse_shorten import reuse
        old={'caps':{'generation':1},'records':[{'record_id':'R03','sha256':'sha'}]}
        archive=self.home/'archive'; save(archive/'plan.json',old)
        Ledger(archive/'paid.jsonl',digest(old),old['caps'])
        g={'source_sha':'sha','partition_key':'채팅','facts':[fact()]}
        save(archive/'subject-shorten/input.json',{'binding':digest(old),'records':{'R03':g}})
        save(archive/'subject-shorten/R03.json',{'input_sha':'key','answer':{'items':[]},'usage':{}})
        save(self.home/'state/plan.json',old);save(self.home/'state/generated/R03.json',g)
        self.assertEqual(reuse(old,self.home,archive)['cached_receipts'],1)
        self.assertTrue((self.home/'state/subject-shorten-cache/R03-key.json').exists())
        save(self.home/'state/generated/R03.json',{**g,'source_sha':'new'})
        self.assertEqual(reuse(old,self.home,archive)['cached_receipts'],0)


if __name__=='__main__': unittest.main()
