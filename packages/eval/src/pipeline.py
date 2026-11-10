"""G16 preparation harness. Default prepare is offline. Paid phases require exact plan SHA approval."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import threading
from uuid import uuid5, NAMESPACE_URL
from safety import HERE, RUNNER, ROOT, Ledger, Paid, Stop, digest, exclusive, load, save
sys.path.insert(0,str(RUNNER))
from local_db import HOST, database
from model_output import ModelOutputInvalid
from prepare import inputs, prepare, scope_home, source_home, default_runs, SCOPES
from generation import (LUNA, MODEL_PREFIX, _FAILURE_LOCK, absorbed_unit, adapters, cached_result, checked_facts, disposition, generate, generated_units, generation_packet, generation_receipt, generation_report, generation_result, generation_task, maintain, packet_model, parse_model, readback, uid)  # moved to the core module, issue #6

ARMS={'off':('0','domain'),'plan':('1','domain'),'subject':('0','subject'),'both':('1','subject')}













def revalidate(plan, ledger):
    """No model object or credential loading: cached paid results are the authority."""
    for r in plan['records']:
        output=HERE/'state/generated'/f"{r['record_id']}.json"
        old=load(output) if output.exists() else {}
        if old and old['source_sha']!=r['sha256']: raise Stop('generated source drift')
        # Never undo a completed one-pass rewrite on a later revalidation.
        if old.get('rewrite_pass'): continue
        receipt=generation_receipt(r,ledger,HERE/'state')
        if receipt:
            result=cached_result(r,receipt)
        elif 'answer' in old:
            result=generation_result(r,old['answer'],old.get('usage'),old.get('raw_response'))
        else: raise Stop('no stored generation for '+r['record_id'])
        save(output,result)
    return generation_report(plan)

def rewrite(plan, paid):
    task=('거절된 fact만 오류 사유에 맞게 고친다. 이미 통과한 fact는 도구가 보존하며 입력에 없다. '
          'E_CLAIM_QUOTE는 식별자가 있는 원문 줄을 근거에 추가하거나 원문에 없는 값만 제거한다. '
          '원문은 줄 번호가 붙어 있다. 원래 index별 정확히 하나의 결과를 반환한다. '
          'JSON: {"items":[{"index":0,"fact":{...원래 fact 필드와 수정된 source_ranges...}} 또는 '
          '{"index":1,"drop":true,"reason":"원문으로 고칠 수 없는 구체 이유"}]}. '
          '규칙 분리가 필요하면 같은 index의 fact 대신 facts:[{...},{...}]를 반환한다. '
          '거절 사실의 교정/분리만 허용하며 새 지식 추가/통과 fact 재작성 금지. 인용/이유를 지어내지 않는다.')
    for r in plan['records']:
        output=HERE/'state/generated'/f"{r['record_id']}.json"
        if not output.exists(): raise Stop('revalidate/generate first')
        g=load(output)
        if g['source_sha']!=r['sha256']: raise Stop('generated source drift')
        if g.get('rewrite_pass') or not g.get('rejected'): continue
        paid.scope='rewrite:'+r['record_id']
        packet={'rejected':g['rejected'],'source': '\n'.join(
            f'{i}: {line}' for i,line in enumerate(generation_packet(r)['source'].splitlines(),1))}
        folder=HERE/'state/packets'/('rewrite-'+r['record_id']); save(folder/'input.json',packet)
        text,usage=paid.model(MODEL_PREFIX+task,str(folder),reserve=.10,step='generation')
        # Persisted paid receipt makes crash/resume reuse this one call, even for invalid JSON.
        try:
            answer=parse_model(text); items=answer.get('items',[])
            if not isinstance(items,list): raise ValueError('rewrite items must be a list')
        except ValueError as exc:
            items=[];g['rewrite_error']=str(exc)
        remaining=[];dropped=[];count=0
        for rejected in g['rejected']:
            matches=[x for x in items if isinstance(x,dict) and type(x.get('index')) is int and x['index']==rejected['index']]
            if len(matches)!=1:
                remaining.append({**rejected,'rewrite_errors':[{'code':'E_REWRITE_ITEM','detail':'missing or duplicate index'}]});continue
            item=matches[0]
            if item.get('drop') is True and isinstance(item.get('reason'),str) and item['reason'].strip() and 'fact' not in item:
                dropped.append({**rejected,'reason':item['reason']});continue
            candidates=item.get('facts') if 'facts' in item else [item.get('fact')]
            if not isinstance(candidates,list) or not candidates or item.get('drop'):
                remaining.append({**rejected,'rewrite_errors':[{'code':'E_REWRITE_ITEM','detail':'invalid correction or drop'}]});continue
            checked=checked_facts(r,{'partition_key':g['partition_key'],'facts':candidates})
            g['facts'].extend(checked['facts']);count+=len(checked['facts'])
            if checked['rejected']:
                remaining.append({**rejected,'attempted_facts':candidates,
                                  'rewrite_errors':checked['rejected']})
        g.update(rejected=remaining,dropped=dropped,rewritten=count,rewrite_pass=1,rewrite_usage=usage)
        g['status']='partial' if g.get('partial') else ('lint-valid' if g['facts'] else 'failed')
        save(output,g)
    return generation_report(plan)

def import_generation(plan, archives):
    """Explicit new-state import; every original paid effect remains charged, including duplicates."""
    import shutil
    if isinstance(archives,(str,Path)): archives=[archives]
    archives=[Path(a).resolve() for a in archives]
    home=HERE.resolve();state=home/'state'
    if state.exists(): raise Stop('import requires absent new state')
    if len(set(archives))!=len(archives): raise Stop('duplicate archive')
    sources=[];covered=set();charged=[]
    for archive in archives:
        if not archive.is_relative_to(home) or archive==state: raise Stop('archive must be inside this scope')
        old_plan=load(archive/'plan.json');old_binding=digest(old_plan)
        if old_plan.get('scope','full')!=plan.get('scope','full') or old_plan['records']!=plan['records']:
            raise Stop('import source inventory/scope drift')
        old=Ledger(archive/'paid.jsonl',old_binding,old_plan['caps'])
        latest={r['id']:r for r in old.rows}
        if not latest or any(r['state']!='done' or r['step']!='generation' for r in latest.values()):
            raise Stop('import requires terminal generation-only ledger')
        mapped=set();records=[]
        for r in plan['records']:
            receipt=generation_receipt(r,old,archive)
            if receipt is not None:
                covered.add(r['record_id']);mapped.add(receipt[0]['id']);records.append(r)
        if mapped!=set(latest): raise Stop('unmapped paid charges; import refused')
        for row in latest.values():
            rel=Path(row['result'])
            if rel!=Path('responses')/(row['id']+'.json'): raise Stop('invalid response path')
            value=load(archive/rel)
            if digest(value)!=row['sha']: raise Stop('paid result drift')
            # Same request made in distinct attempts is TWO paid effects, never deduplicate its cost.
            ident=digest(['imported-generation',old_binding,row['id']])
            charged.append((archive,old_binding,row,ident,value))
        sources.append((archive,records))
    if covered!={r['record_id'] for r in plan['records']}:
        raise Stop('missing generation receipts; include earlier --from-state archives')
    if len({x[3] for x in charged})!=len(charged): raise Stop('overlapping archive ledger effects')
    staged=home/'state.importing'
    if staged.exists(): raise Stop('incomplete import staging; inspect before retry')
    staged.mkdir();ledger=Ledger(staged/'paid.jsonl',digest(plan),plan['caps'])
    for archive,old_binding,row,ident,value in charged:
        result='responses/'+ident+'.json';save(staged/result,value)
        ledger.append({**row,'id':ident,'result':result,'imported_from':{
            'state':str(archive.relative_to(ROOT)),'binding':old_binding,
            'id':row.get('imported_from',{}).get('id',row['id']),'at':row.get('at'),
            'result':row['result']}})
    # Later archive wins the generation answer for overlapping records, never the cost.
    for archive,records in sources:
        for r in records:
            name='generate-'+r['record_id']
            shutil.copytree(archive/'packets'/name,staged/'packets'/name,dirs_exist_ok=True)
    save(staged/'import.json',{'imported_from':[str(a.relative_to(ROOT)) for a in archives],
                             'binding':digest(plan),'records':sorted(covered),'paid_effects':len(charged)})
    os.replace(staged,state)
    return {'imported_records':len(covered),'paid_effects':len(charged),
            'charged_usd':ledger.spent('generation')}

def import_state(plan, archives, *, empty_db=False, continue_checkpoint=False):
    """Carry one complete checkpoint across a code-only plan revision, offline."""
    import shutil
    if isinstance(archives,(str,Path)): archives=[archives]
    if len(archives)!=1: raise Stop('import-state requires one complete archive; do not double-charge its ancestors')
    home=HERE.resolve(); archive=Path(archives[0]).resolve(); state=home/'state'
    if state.exists(): raise Stop('import requires absent new state')
    allowed_root=home.parent if plan.get('area_catalog') else home
    if not archive.is_relative_to(allowed_root) or archive==state: raise Stop('archive must be inside the approved scope root')
    if any(p.is_symlink() for p in archive.rglob('*')): raise Stop('archive symlinks forbidden')
    old_plan=load(archive/'plan.json'); old_binding=digest(old_plan)
    if empty_db and continue_checkpoint:
        raise Stop('choose exactly one resume mode')
    if continue_checkpoint and plan.get('resume_mode') != 'continue-rebuilt-checkpoint':
        raise Stop('plan does not authorize checkpoint continuation')
    if plan.get('resume_mode') == 'continue-rebuilt-checkpoint' and not continue_checkpoint:
        raise Stop('checkpoint continuation requires --continue-checkpoint')
    if plan.get('resume_mode') == 'empty-db-paid-cache' and not empty_db:

        raise Stop('hierarchy resume requires --empty-db; absorbed checkpoint is evidence only')
    keys = ('records','questions','scope','host','container','fresh_database')
    if plan.get('area_catalog'):
        if not empty_db or old_plan.get('scope')!='pilot-feature': raise Stop('area rebuild requires latest pilot-feature and --empty-db')
        old_records={r['record_id']:r for r in old_plan['records']}
        if any(old_records.get(r['record_id'])!=r for r in plan['records']): raise Stop('area subset inventory drift')
        keys=('questions','fresh_database')
    if not empty_db: keys += ('subject_contract',)
    elif plan.get('subject_contract',{}).get('version') not in ((3,4,5,6,7,8,9) if plan.get('area_catalog') else (3,)) or old_plan.get('subject_contract',{}).get('version') not in (2,3):
        raise Stop('unsupported subject contract rebuild')
    for key in keys:
        if old_plan.get(key)!=plan.get(key): raise Stop('import source inventory/scope drift: '+key)
    old=Ledger(archive/'paid.jsonl',old_binding,old_plan['caps'])
    latest={r['id']:r for r in old.rows}
    if not latest or any(r['state'] not in ('done','released') for r in latest.values()):
        raise Stop('import requires all paid effects done or Main-released; uncertain rows forbid resume')
    for row in latest.values():
        if row['step'] not in plan['caps']: raise Stop('import step missing admission cap')
        if row['state']=='released': continue
        if Path(row['result'])!=Path('responses')/(row['id']+'.json'): raise Stop('invalid response path')
        if digest(load(archive/row['result']))!=row['sha']: raise Stop('paid result drift')
    for record in plan['records']:
        path=archive/'generated'/f"{record['record_id']}.json"
        if path.exists() and load(path)['source_sha']!=record['sha256']: raise Stop('generated source drift')
    dump=archive/'database.dump'; meta=archive/'database.json'; metadata=None
    if dump.exists()!=meta.exists(): raise Stop('incomplete database checkpoint')
    if dump.exists():
        metadata=load(meta)
        if metadata['binding']!=old_binding or metadata['sha256']!=hashlib.sha256(dump.read_bytes()).hexdigest():
            raise Stop('local dump binding mismatch')
    else:
        if any(not (archive/'generated'/f"{r['record_id']}.json").exists() for r in plan['records']):
            raise Stop('empty DB resume requires all generated records')
    if empty_db and any(not (archive/'generated'/f"{r['record_id']}.json").exists() for r in plan['records']):
        raise Stop('empty DB rebuild requires all generated records')
    if continue_checkpoint:
        from local_db import MIGRATIONS
        if any(old_plan.get('code',{}).get('migrations/'+m+'.sql') != plan.get('code',{}).get('migrations/'+m+'.sql') for m in MIGRATIONS):
            raise Stop('checkpoint migration code drift')
        if metadata is None or metadata.get('migrations') != MIGRATIONS or plan.get('subject_contract',{}).get('version') != 3:
            raise Stop('continuation requires complete rebuilt hierarchy checkpoint')
        for record in plan['records']:
            g=load(archive/'generated'/f"{record['record_id']}.json")
            if g.get('subject_fix_version') != 2 or g.get('subject_refix_version') != 2:
                raise Stop('continuation requires completed subject-fix/refix; never mutate absorbed facts')
    staged=home/'state.importing'
    if staged.exists(): raise Stop('incomplete import staging; inspect before retry')
    if empty_db:
        staged.mkdir()
        for name in ('responses','packets','embedding-cache'):
            if (archive/name).exists(): shutil.copytree(archive/name,staged/name)
        for record in plan['records']:
            value=load(archive/'generated'/f"{record['record_id']}.json")
            if plan.get('area_catalog'):
                from area_catalog import relabel
                value=relabel(record,value,source_home(home)/'v1')
            save(staged/'generated'/f"{record['record_id']}.json",value)
        metadata=None  # dump was verified above, but stays only in the archive
    else:
        shutil.copytree(archive,staged,ignore=shutil.ignore_patterns('paid.jsonl','plan.json'))
    ledger=Ledger(staged/'paid.jsonl',digest(plan),plan['caps'])
    # Preserve released reservation history too: repeated attempts remain charged.
    for row in (r for r in old.rows if r['state'] in ('done','released')):
        ledger.append({**row,'imported_from':{
            'state':str(archive.relative_to(ROOT)),'binding':old_binding,
            'id':row.get('imported_from',{}).get('id',row['id']),
            'result':row.get('result'),'at':row.get('at'),'prior':row.get('imported_from')}})
    if metadata is not None:
        save(staged/'database.json',{**metadata,'binding':digest(plan),
             'progress_bindings':list(dict.fromkeys(metadata.get('progress_bindings',[])+[old_binding])),
             'imported_from':{'binding':old_binding,'sha256':metadata['sha256']}})
    save(staged/'plan.json',plan)
    result={'imported_records':len(list((staged/'generated').glob('*.json'))),
            'paid_effects':len(latest),'charged_usd':{s:ledger.spent(s) for s in plan['caps']},
            'database_imported':metadata is not None,'database_rebuild':metadata is None,
            'imported_from':str(archive.relative_to(ROOT))}
    save(staged/'import.json',result)
    os.replace(staged,state)
    return result


def resume_maintenance(plan,dsn,unit,maintenance):
    """Absorption commits before metadata maintenance; recover that transaction gap.

    Keep the original record's paid scope, so rolled-back description/merge calls
    hit their exact journal keys rather than the final coordinator's domain scope.
    """
    import store_pg as pg, subject_dict as sd
    with pg.connect(dsn) as conn,conn.cursor() as cur:
        cur.execute('''select p.domain from knowledge.digest_part p
            join knowledge.ledger_entry e using(entry_id)
            join knowledge.absorption a on a.entry_id=p.entry_id and a.fact_index=p.fact_index
            join knowledge.domain_type d on d.host_id=p.host_id and d.domain=p.domain
            where e.work_unit_id=%s and d.merged_into is null
            and a.decision::text in ('absorbed','retained_as_evidence','rejected')
            group by p.domain,d.description_updated_at
            having d.description_updated_at is null or max(a.created_at)>d.description_updated_at
            order by p.domain''',(unit,))
        domains=[row[0] for row in cur.fetchall()]
    for domain in domains:
        step='resume-maintenance:'+unit+':'+domain
        with pg.connect(dsn) as conn,conn.cursor() as cur:
            cur.execute('select binding from g16_experiment.progress where step=%s',(step,))
            if cur.fetchone(): continue
            sd.maintain(cur,HOST,domain,**maintenance)
            cur.execute('insert into g16_experiment.progress values(%s,%s)',(step,digest(plan)))




def absorb(plan,dsn,paid,cfg):
    if plan.get('write_path'):
        from place_write_absorb import absorb as absorb_written
        return absorb_written(plan,HERE,dsn,paid,cfg,HOST)
    import knowledge_cli as cli, store_pg as pg, worktime_driver as wd, digest_driver as dd
    import embed
    from subject_ground import assert_completed as assert_grounded
    assert_grounded(plan,HERE)  # before ANY DB effect, including resumed ledger paths
    if plan.get('embedding_precompute'):
        from precompute import assert_completed
        assert_completed(plan,HERE)
    if plan.get('embedding_transition'):
        from reembed import assert_ready
        assert_ready(plan,HERE,dsn)
    if plan.get('area_catalog'):
        from area_catalog import install
        install(plan,HERE,dsn,HOST,embed.encode_queries)
    from subject_shorten import assert_completed as assert_shortened
    assert_shortened(plan,HERE)
    before=embed.CACHE.stats()
    adapt,maintenance=adapters(cfg,paid)
    if plan.get('area_catalog'):
        from area_catalog import name_area
        adapt['name_area']=lambda text, hint: name_area(paid,text,hint)
    for r in plan['records']:
        paid.scope=r['record_id']; p=HERE/'state/generated'/f"{r['record_id']}.json"
        if not p.exists() or load(p)['status'] not in ('lint-valid','partial'): continue
        if not load(p).get('facts'): continue
        g=load(p); unit=uid(r)
        if plan.get('resume_mode') in ('empty-db-paid-cache','continue-rebuilt-checkpoint') and g.get('subject_refix_version') != 2:
            raise Stop('subject-refix must complete before hierarchy absorption')
        if plan.get('fresh_database') and g.get('subject_fix_version') != 2:
            raise Stop('subject-fix must complete before fresh absorption')
        for unit, scope, facts in ([(uid(r),r['record_id'],g['facts'])] if plan.get('area_catalog') else generated_units(r,g)):
            paid.scope=scope
            if plan.get('area_catalog'):
                from area_absorb import assigned_area
                area = assigned_area(plan, HERE, dsn, HOST, r, g, adapt)
                facts=[{**f,'domain':area,'assigned_area':area,'domain_is_hint':False} for f in facts]
            if plan.get('resume_mode') == 'continue-rebuilt-checkpoint' and absorbed_unit(dsn,unit,len(facts)):
                # Canonical commit may precede vector persistence in the saved checkpoint.
                # Materialize only missing vectors (precompute cache), never rejudge/reabsorb.
                pg.embed_unit(dsn,unit)
                continue
            with pg.connect(dsn) as conn,conn.cursor() as cur:
                cur.execute('select entry_id::text from knowledge.ledger_entry where work_unit_id=%s',(unit,))
                entries=[row[0] for row in cur.fetchall()]
                if entries and plan.get('area_catalog'):
                    cur.execute('select judgement_body from knowledge.ledger_entry where work_unit_id=%s', (unit,))
                    bodies=[row[0] for row in cur.fetchall()]
                    if len(bodies) != 1 or 'area_validation' not in bodies[0]:
                        raise Stop('area checkpoint lacks transactional fact disposition')
                    save(HERE/'state/fact-dispositions'/f"{scope}.json",
                         {**bodies[0]['area_validation'], 'input_sha':digest(g)})
            if not entries:
                options = {}
                if plan.get('area_catalog'):
                    options = dict(assigned_area=area, isolate_invalid=True,
                                   source_text=(source_home(HERE)/'v1'/r['path']).read_text())
                    if plan.get('subject_ground'):
                        from subject_ground import record_check
                        options['grounding_check'] = record_check(plan,HERE,r,g)
                result=cli.record(dsn,{'host_id':HOST,'work_unit_id':unit,'partition_key':g['partition_key'],
                                      'facts':facts},same=None if plan.get('area_catalog') else adapt['same'],
                                  embed_fn=adapt['embed_fn'], **options)
                if plan.get('area_catalog'):
                    save(HERE/'state/fact-dispositions'/f"{scope}.json",
                         {**result, 'input_sha':digest(g), 'assigned_area':area})
                if result.get('errors'):
                    save(HERE/'state/record-errors'/f"{scope}.json",result); continue
                if not result.get('entry_id'): continue
                entries=[result['entry_id']]
            # Imported original units and new recovered facts progress independently.
            wd.run_worktime(dsn,len(entries),dd._jev_judge,entry_ids=entries)
            with pg.connect(dsn) as conn,conn.cursor() as cur:
                cur.execute('select status::text from knowledge.work_unit where work_unit_id=%s',(unit,))
                status=cur.fetchone()[0]
            if status=='active': pg.complete(dsn,unit)
            # The document has already been routed against ALL facts. Exact assigned
            # domain reuse avoids a second Jev call on the surviving subset.
            digest_adapt = {k:v for k,v in adapt.items() if k != 'name_area'} if plan.get('area_catalog') else adapt
            dd.run_digestion(dsn,unit,dd._jev_judge,maintenance=None,workers=1,**digest_adapt)
    result=maintain(plan,dsn,paid,cfg,checkpoint='domain-batch')
    result['embedding_cache']={k:embed.CACHE.stats()[k]-before[k] for k in before}
    save(HERE/'state/embed-absorb-stats.json',result['embedding_cache'])
    return result




def quality(plan,dsn,paid):
    import store_pg as pg
    report=readback(plan,dsn); packets=[]
    # Stated stratified sample: all restored pilots (55), plus SHA-ordered nonpilots (65), 120 source records.
    sample=[r for r in plan['records'] if r['pilot']]+sorted([r for r in plan['records'] if not r['pilot']],key=lambda r:digest(r['record_id']))[:65]
    byid={r['record_id']:r for r in report['records']}
    for r in sample:
        packets.append(('domain',r['record_id'],{'source':(source_home(HERE)/'v1'/r['path']).read_text(),'readback':byid[r['record_id']],
                                               'domains':report['tables']['domain_type']}))
    # Every surviving subject is presented by domain with its fragments; no lexical candidate filter.
    with pg.connect(dsn) as conn,conn.cursor() as cur:
        for domain in report['tables']['domain_type']:
            cur.execute('select subject_id::text,text,source from knowledge.fragment where host_id=%s and domain=%s and active order by subject_id,id',(HOST,domain['domain']))
            packets.append(('subjects',domain['domain'],{'domain':domain,'subjects':[s for s in report['tables']['subject'] if s['domain']==domain['domain']], 'fragments':cur.fetchall()}))
    for table in ('subject_merge','domain_merge'):
        for merge in report['tables'][table]:
            if merge.get('actor')=='luna-auto': packets.append(('merge',str(merge['merge_id']),merge))
    outcomes=[]
    for kind in ('domain','subjects','merge'):
        selected=[x for x in packets if x[0]==kind]; batch_size=1 if kind=='subjects' else 10
        for start in range(0,len(selected),batch_size):
            batch=selected[start:start+batch_size]; paid.scope=kind+':'+str(start)
            result,usage=packet_model(paid,'quality-'+digest([kind,start])[:24],
              {'kind':kind,'items':[{'id':key,'evidence':packet} for _,key,packet in batch]},
              'Independent Sol audit. Return JSON {"items":[{"id":"exact input id", '
              '"verdict":"correct|incorrect|unclear", "reason":"...", "issues":[], "review_list":[]}]}. '
              'One result per input id, no omissions. For domain inspect source facts vs assigned areas; '
              'count missing/held separately. For subjects identify duplicate or wrongly split same objects WITHIN '
              'domain and incorrect cross-domain leaf ownership. For merge independently verify same actual '
              'object/area, not related/part-of, using receipt evidence. Missing evidence => unclear, never correct. '
              'Give concrete source/fragment references.',sol=True)
            items=result.get('items',[])
            if len(items)!=len(batch) or {x.get('id') for x in items}!={x[1] for x in batch}:
                raise Stop('Sol omitted/duplicated audit item')
            if any(x.get('verdict') not in ('correct','incorrect','unclear') for x in items): raise Stop('invalid Sol result')
            outcomes.extend({'kind':kind,**x,'batch_usage':usage} for x in items)
    merges=[x for x in outcomes if x['kind']=='merge']; domains=[x for x in outcomes if x['kind']=='domain']
    result={'sample':{'pilots':sum(bool(r['pilot']) for r in sample),'nonpilots':sum(not r['pilot'] for r in sample),'method':'all pilots + up to 65 SHA256-ordered nonpilots'},'outcomes':outcomes,
            'domain_errors':sum(x['verdict']=='incorrect' for x in domains),'domain_n':len(domains),
            'merge_precision':sum(x['verdict']=='correct' for x in merges)/len(merges) if merges else None,
            'merge_n':len(merges),'merge_unclear':sum(x['verdict']=='unclear' for x in merges),
            'holds':report['tables']['merge_judgement'],
            'hold_event_count':len(report['tables']['merge_judgement']),
            'review_flagged_events':sum(bool(x['review_list']) for x in report['tables']['merge_judgement']),
            'review_list':report['review_list'],
            'g5_reference':{'misassignment':.033,'n':60,'caveat':'different corpus and labels; not paired acceptance'}}
    save(HERE/'state/quality.json',result);return result

def reading(plan,dsn,paid,runs):
    import brief, worker_tools as wt, config
    report=readback(plan,dsn)
    if not report['fragments_per_domain_subject']: raise Stop('no DB fragments to measure')
    if any(not (HERE/'state/generated'/f"{r['record_id']}.json").exists() for r in plan['records']):
        raise Stop('source generation inventory unfinished')
    # Holds/failures remain in the frozen denominator and report, never silently called complete.
    snapshot=digest(report)
    frozen=HERE/'state/reading-corpus.json'
    if frozen.exists() and load(frozen)!= {'sha':snapshot,'runs':runs}: raise Stop('reading corpus/run-count drift')
    save(frozen,{'sha':snapshot,'runs':runs})
    os.environ.update(LH_BRIEF_MAPREDUCE='0',LH_READ_STATS='1',LH_BRIEF_LIMIT='1',LH_DOMAIN_EXPAND_CAP='150',LH_SUBJECT_EXPAND_CAP='150',LH_BRIEF_SPLIT='0',LH_BRIEF_VERBATIM='0')
    ask=wt.make_ask(config.require('jev_api_key','jev_base_url','jev_model'))
    for run in range(1,runs+1):
        for q in plan['questions']:
            # Rotate arm order per question/run to reduce cache/time ordering confounds.
            names=list(ARMS); shift=(run+int(digest(q['qid'])[:4],16))%4; names=names[shift:]+names[:shift]
            for arm in names:
                paid.scope=f'{arm}:{run}:{q["qid"]}'; output=HERE/'state/reading'/arm/str(run)/f"{q['qid']}.json"
                if output.exists(): continue
                os.environ['LH_READ_PLAN'],os.environ['LH_EXPAND']=ARMS[arm]
                start=time.monotonic()
                result=brief.brief(dsn,HOST,q['question'],q['queries'],ask,runner=paid.model)
                save(output,{'arm':arm,'run':run,'qid':q['qid'],'corpus':snapshot,'seconds':time.monotonic()-start,**result})

def chunks(text,size=6000):
    # Hard chunk cap even for one long line; same max-over-chunks scoring as G14.
    return [text[i:i+size] for i in range(0,len(text),size)] or ['']

def judging(plan,paid,runs,cfg):
    import urllib.request
    out=[]
    for arm in ARMS:
        for run in range(1,runs+1):
            for q in plan['questions']:
                path=HERE/'state/reading'/arm/str(run)/f"{q['qid']}.json"
                if not path.exists(): raise Stop('missing reading result: '+str(path))
                result=load(path); scores=[[0.,0.] for _ in q['nuggets']]
                for chunkno,text in enumerate(chunks(result['brief'])):
                    for start in range(0,len(scores),10):
                        paid.scope=f'{arm}:{run}:{q["qid"]}:{chunkno}:{start}'
                        claims=q['claims'][start:start+10]; questions={}
                        for i in range(len(claims)):
                            questions['c'+str(i)]={'type':'noul','instructions':f'nuggets[{i}] 의 내용이 answer 에 담겨 있는가? 조건·예외·순서·값까지 같은 뜻이어야 한다. 표현이 달라도 뜻이 같으면 true, 빠졌거나 일부만이면 false.','criteria':{'true':'담겨 있다','false':'담겨 있지 않다'}}
                            questions['x'+str(i)]={'type':'noul','instructions':f'answer 에 nuggets[{i}] 와 모순되는(반대거나 다른 값을 말하는) 내용이 있는가?','criteria':{'true':'모순된다','false':'모순되지 않는다'}}
                        body={'model':cfg['jev_model'],'state':{'question':q['question'],'answer':text,'nuggets':[{'i':i,'text':c} for i,c in enumerate(claims)]},'questions':questions}
                        req=urllib.request.Request(cfg['jev_base_url'].rstrip('/')+'/v1/systemone',data=json.dumps(body,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+cfg['jev_api_key'],'Content-Type':'application/json'})
                        with urllib.request.urlopen(req,timeout=120) as response: answers=json.load(response)['answers']
                        for i in range(len(claims)):
                            scores[start+i]=[max(scores[start+i][0],answers['c'+str(i)]['noul']),max(scores[start+i][1],answers['x'+str(i)]['noul'])]
                for nugget,score in zip(q['nuggets'],scores): out.append({'arm':arm,'run':run,'qid':q['qid'],'nugget':nugget,'covered':score[0],'contradicted':score[1]})
    summary={}
    for arm in ARMS:
        summary[arm]=[]
        for run in range(1,runs+1):
            rows=[r for r in out if r['arm']==arm and r['run']==run]
            summary[arm].append({'run':run,'n':len(rows),'coverage':sum(r['covered']>=.7 for r in rows)/len(rows),'contradictions':sum(r['contradicted']>=.7 for r in rows)})
    from collections import Counter
    stages={}; timings={}
    for arm in ARMS:
        counts=Counter(); rows=[]
        for path in sorted((HERE/'state/reading'/arm).glob('*/*.json')):
            item=load(path);rows.append(item)
            counts.update(v['stage'] for v in item.get('collection_stats',{}).get('added',{}).values())
        stages[arm]=dict(counts)
        timings[arm]={'questions':len(rows),'seconds':sum(x['seconds'] for x in rows),
                      'writer_usd_all_turns':sum(x['writer_cost_usd'] for x in rows)}
    save(HERE/'state/judged.json',{'rows':out,'summary':summary,'baselines64':plan['baseline'],
         'stage_first_add_counts':stages,'timing_and_writer_cost':timings,
         'attribution_limit':'First-add stages are exact collection evidence, not nugget-level causal rescue. Raw seen/stop/cap traces retained per question.'})
    return summary

def verify_plan(plan):
    records,qs=inputs(plan.get('scope','full'))
    if records!=plan['records'] or qs!=plan['questions']: raise Stop('source/key drift')
    for path,sha in plan['code'].items():
        if hashlib.sha256((RUNNER/path).read_bytes()).hexdigest()!=sha: raise Stop('code drift: '+path)

def main():
    global HERE, HOST
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['init-write','place','write','ground','report','evaluate-reading','prepare','import-generation','import-state','import-fresh','revalidate','generate','rewrite','subject-fix','subject-refix','subject-shorten','subject-ground','embed-precompute','calibrate','reembed','area-catalog','resolve-reservation','absorb','maintain','readback','quality','read','judge'])
    parser.add_argument('--approve-plan-sha',help='Main only, after explicit cost approval; no default')
    parser.add_argument('--runs',type=int,choices=[1,3])
    parser.add_argument('--scope',choices=SCOPES,default='full')
    parser.add_argument('--continue-checkpoint',action='store_true')
    parser.add_argument('--reservation-id')
    parser.add_argument('--count-as-spent',action='store_true')
    parser.add_argument('--reason')
    parser.add_argument('--empty-db',action='store_true',help='keep archived checkpoint as evidence; import paid cache only')
    parser.add_argument('--from-state',type=Path,action='append',help='archive with original plan.json; repeat oldest first')
    args=parser.parse_args()
    args.runs=args.runs or default_runs(args.scope)
    if args.scope!='full' and args.runs!=1: raise Stop('pilot approval is one run only')
    HERE=scope_home(args.scope)
    HOST='knowledge-local-'+args.scope if args.scope!='full' else 'knowledge-local'
    expected_root=os.environ.get('LH_KNOWLEDGE_WORKER_ROOT')
    if expected_root and ROOT!=Path(expected_root): raise Stop('wrong worker root')
    if args.phase=='prepare': print(json.dumps(prepare(args.scope),ensure_ascii=False,indent=2));return
    plan=load(HERE/'plan.json')
    if plan.get('scope','full')!=args.scope: raise Stop('scope/plan mismatch')
    if args.phase=='resolve-reservation':
        # Recovery must work with the OLD immutable plan even when current code has changed.
        if not args.reservation_id or not args.reason: raise Stop('--reservation-id and --reason required')
        if load(HERE/'state/plan.json') != plan: raise Stop('state/plan mismatch')
        from jev_failure import resolve
        with exclusive(HERE):
            ledger=Ledger(HERE/'state/paid.jsonl',digest(plan),plan['caps'])
            rows=ledger._effect_rows(args.reservation_id)
            if rows and rows[-1]['step']=='embedding' and args.count_as_spent:
                ledger.release(args.reservation_id,args.reason)
                result={'status':'embedding-conservative-spent','id':args.reservation_id}
            else: result=resolve(ledger,args.reservation_id,args.reason,count_as_spent=args.count_as_spent)
        print(json.dumps(result,ensure_ascii=False,indent=2));return
    verify_plan(plan); binding=digest(plan)
    if not plan.get('write_path') and args.phase in ('init-write','place','write','ground','report'):
        raise Stop('phase requires a freshly prepared unit-first plan')
    if plan.get('write_path') and args.phase in ('init-write','report'):
        with exclusive(HERE):
            if args.phase=='init-write':
                from place_write import initialize
                result=initialize(plan,HERE)
            else:
                from report import report
                result=report(HERE)
        print(json.dumps(result,ensure_ascii=False,indent=2));return
    if plan.get('write_path') and args.phase in ('generate','rewrite','revalidate','import-generation','import-state','import-fresh','subject-fix','subject-refix','subject-shorten','subject-ground','area-catalog'):
        raise Stop('unit-first path requires fresh judge/place/write/ground; legacy identity phases forbidden')
    if plan.get('area_catalog') and args.phase in ('absorb','maintain','embed-precompute','subject-shorten','subject-ground'):
        from area_catalog import confirmed
        confirmed(plan,HERE)
    if plan.get('area_catalog') and args.phase in ('generate','rewrite','subject-fix','subject-refix'):
        raise Stop('area rebuild must reuse facts and paid subject corrections; regeneration forbidden')
    if plan.get('fresh_database') and args.phase in ('generate','rewrite','revalidate','import-generation'):
        raise Stop('fresh pilot reuses paid generation; use import-fresh initially or import-state for same-scope resume, never regenerate')
    if args.phase not in ('readback','revalidate','import-generation','import-state','import-fresh') and args.approve_plan_sha!=binding: raise Stop('paid approval missing or wrong plan SHA')
    if args.phase in ('revalidate','import-generation','import-state','import-fresh'):
        with exclusive(HERE):
            if args.phase in ('import-generation','import-state','import-fresh'):
                if args.from_state is None: raise Stop('--from-state required')
                if args.phase=='import-fresh':
                    from fresh import import_fresh
                    result=import_fresh(plan,args.from_state,HERE)
                else: result=import_state(plan,args.from_state,empty_db=args.empty_db,continue_checkpoint=args.continue_checkpoint) if args.phase=='import-state' else import_generation(plan,args.from_state)
            else:
                result=revalidate(plan,Ledger(HERE/'state/paid.jsonl',binding,plan['caps']))
        print(json.dumps(result,ensure_ascii=False,indent=2));return
    # Do not load the global knowledge.json (which can bind the remote V2 database).
    save(HERE/'state/config.json',{})
    os.environ.update(LH_KNOWLEDGE_CONFIG=str(HERE/'state/config.json'),LH_KNOWLEDGE_DEFAULT_HOST=HOST,
                      LH_KNOWLEDGE_DB_URL='postgresql://invalid/forbidden',LH_EMBED_PROVIDER='openrouter',
                      LH_KNOWLEDGE_SEARCH_DIR=str(HERE/'state/search'),
                      LH_EMBED_THRESHOLDS=str(HERE/'state/embedding-thresholds.json'))
    import config
    import embed
    embed.configure_cache(HERE/'state/embedding-cache')
    if plan.get('embedding_policy'):
        import embedding_transport as et
        actual=et.policy()
        if actual != plan['embedding_policy']: raise Stop('embedding environment/plan drift')
    if plan.get('candidate_policy'):
        from candidate_policy import policy
        if policy() != plan['candidate_policy']: raise Stop('candidate environment/plan drift')
    cfg=config.require('jev_api_key','jev_base_url','jev_model') if args.phase not in ('readback','generate','rewrite','subject-fix','subject-refix','subject-shorten','calibrate','area-catalog','write') else config.load()
    ledger=Ledger(HERE/'state/paid.jsonl',binding,plan['caps'],subscription_limits=plan.get('subscription_limits'),cash_limit=(plan['cash_caps']['total_usd']-plan['cash_caps'].get('prior_spent_usd',0)) if 'cash_caps' in plan else None)
    step={'place':'absorption','write':'generation','ground':'absorption','evaluate-reading':'judging','subject-ground':'absorption','subject-shorten':'generation','area-catalog':'maintenance','generate':'generation','rewrite':'generation','subject-fix':'generation','subject-refix':'generation','embed-precompute':'embedding','calibrate':'embedding','reembed':'embedding','absorb':'absorption','maintain':'maintenance','quality':'quality','read':'reading','judge':'judging','readback':'absorption'}[args.phase]
    if plan.get('write_path') and args.phase=='judge': step='absorption'  # serial dispatcher; Luna uses subscription generation explicitly
    with exclusive(HERE), Paid(ledger,step,cfg) as paid:
        if plan.get('write_path') and args.phase in ('judge','place','write','ground'):
            from place_write import run, production
            result=run(args.phase,plan,HERE,paid,production(paid,cfg))
        elif args.phase=='area-catalog':
            from area_catalog import draft
            # Use all 55 pilot documents even for the ten-document screen.
            result=draft(plan,HERE,paid,inputs('pilot-area')[0])
        elif args.phase=='generate': result=generate(plan,paid)
        elif args.phase=='rewrite': result=rewrite(plan,paid)
        elif args.phase=='subject-fix':
            from objects import subject_fix
            result=subject_fix(plan,paid,HERE)
        elif args.phase=='subject-refix':
            from objects import subject_refix
            result=subject_refix(plan,paid,HERE)
        elif args.phase=='subject-shorten':
            from subject_shorten import run
            result=run(plan,HERE,paid)
        elif args.phase=='subject-ground':
            from subject_ground import run
            from worker_tools import make_ask
            result=run(plan,HERE,paid,make_ask(cfg))
        elif args.phase=='embed-precompute':
            from precompute import run
            result=run(plan,HERE,paid)
        elif args.phase in ('judge','evaluate-reading'): result=judging(plan,paid,args.runs,cfg)
        else:
            if plan.get('write_path') and args.phase=='absorb':
                from place_write import assert_completed as grounded
                from precompute import assert_completed as precomputed
                grounded(plan,HERE); precomputed(plan,HERE)  # before container/DB creation
            with database(binding,home=HERE,name=plan.get('container'),host=HOST) as dsn:
                if plan.get('embedding_transition') and args.phase in ('maintain','quality','read'):
                    from reembed import assert_ready
                    assert_ready(plan,HERE,dsn)
                if args.phase=='calibrate':
                    from calibrate import run
                    result=run(plan,HERE,paid,dsn)
                elif args.phase=='reembed':
                    from reembed import run
                    result=run(plan,HERE,paid,dsn)
                elif args.phase=='absorb': result=absorb(plan,dsn,paid,cfg)
                elif args.phase=='maintain': result=maintain(plan,dsn,paid,cfg)
                elif args.phase=='readback': result=readback(plan,dsn)
                elif args.phase=='quality': result=quality(plan,dsn,paid)
                else: result=reading(plan,dsn,paid,args.runs)
        print(json.dumps({'phase':args.phase,'status':result.get('status') if isinstance(result,dict) else None,'counts':result.get('counts') if isinstance(result,dict) else None,
                          'generation_totals':result.get('totals') if isinstance(result,dict) else None,
                          'subject_refix':result.get('subject_refix') if isinstance(result,dict) else None,
                          'short_name_failures':len(result.get('failures',[])) if args.phase=='subject-shorten' else None,
                          'embedding_cache':result.get('embedding_cache',result.get('cache')) if isinstance(result,dict) else None,
                          'cash_usd':{s:ledger.spent(s,'cash') for s in plan['caps']}},ensure_ascii=False))

if __name__=='__main__':
    from embedding_models import ModelMismatch
    from embedding_thresholds import CalibrationRequired
    try: main()
    except (Stop,ModelMismatch,CalibrationRequired) as exc: print('STOP: '+str(exc),file=sys.stderr);sys.exit(2)
