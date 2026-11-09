"""Offline fresh pilot import: retain paid evidence and accounting, NEVER the old DB."""
import hashlib
import shutil
from pathlib import Path
from safety import Ledger, Stop, digest, load, save, ROOT


def import_fresh(plan, archives, home):
    import pipeline as p
    from objects import raw_fact
    import runner
    if len(archives)!=1: raise Stop('use one complete latest state, not its ancestors')
    archive=Path(archives[0]).resolve();home=home.resolve();state=home/'state'
    if not archive.is_relative_to(home.parent) or archive==state: raise Stop('root-local sibling archive required')
    if state.exists(): raise Stop('fresh import requires absent state')
    if any(x.is_symlink() for x in archive.rglob('*')): raise Stop('archive symlinks forbidden')
    old_plan=load(archive/'plan.json')
    if old_plan['records']!=plan['records'] or old_plan['questions']!=plan['questions']:
        raise Stop('fresh import inventory drift')
    old=Ledger(archive/'paid.jsonl',digest(old_plan),old_plan['caps'])
    latest={r['id']:r for r in old.rows}
    if not latest or any(r['state'] not in ('done','released') for r in latest.values()):
        raise Stop('unresolved historical paid effect')
    values={}
    for row in latest.values():
        if row['state']=='released': continue
        if Path(row['result'])!=Path('responses')/(row['id']+'.json'): raise Stop('response path invalid')
        value=load(archive/row['result'])
        if digest(value)!=row['sha']: raise Stop('paid response drift')
        values[row['result']]=value
    generated={}
    rewrite_task=next(c for c in p.rewrite.__code__.co_consts if isinstance(c,str) and c.startswith('거절된 fact만'))
    for record in plan['records']:
        rid=record['record_id'];g=load(archive/'generated'/f'{rid}.json')
        if g['source_sha']!=record['sha256']: raise Stop('generated source drift')
        original=values.get(g.get('raw_response'))
        if original is None: raise Stop('generation receipt missing: '+rid)
        try: answer=p.parse_model(original['text'])
        except ValueError:
            if g.get('facts'): raise Stop('invalid JSON cannot prove generated facts')
            generated[rid]=g;continue
        if answer!=g.get('answer'): raise Stop('generation answer drift')
        packet=load(archive/'packets'/('generate-'+rid)/'input.json')
        expected=p.generation_packet(record)
        if any(packet.get(k)!=expected[k] for k in ('record','source')): raise Stop('generation source packet drift')
        candidates=list(answer.get('facts',[]))
        if g.get('rewrite_pass'):
            folder=archive/'packets'/('rewrite-'+rid)
            files={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in folder.iterdir() if f.is_file()}
            ident=digest(['generation',['rewrite:'+rid,p.LUNA,p.MODEL_PREFIX+rewrite_task,files]])
            row=next((r for r in latest.values() if r['id']==ident or r.get('imported_from',{}).get('id')==ident or r.get('imported_from',{}).get('original_id')==ident),None)
            if row is None or row['state']!='done': raise Stop('rewrite receipt missing: '+rid)
            try: rewritten=p.parse_model(values[row['result']]['text'])
            except ValueError: rewritten={}
            for item in rewritten.get('items',[]):
                if isinstance(item,dict): candidates.extend(item.get('facts',[]) if 'facts' in item else [item.get('fact')])
        # Derived output cannot introduce or alter claims outside the hash-verified paid responses.
        def semantic(f):
            # Historical generation used the old form-only subject repair. Reconstruct that
            # exact derivation as import proof; NEVER apply it to new generation/subject-fix.
            f=dict(f);text=str(f.get('fact',''));subject=f.get('subject')
            if not (isinstance(subject,str) and subject.strip() and subject in text):
                head=runner.SUBJECT_HEAD.match(text)
                new=head.group(1).strip() if head else text[:40].strip()
                if new:f['subject']=new
            return {k:f.get(k) for k in ('subject','subject_type','subject_description','fact','kind','why','reason','group','aliases')}
        allowed=[semantic(f) for f in candidates if isinstance(f,dict)]
        for fact in g.get('facts',[]):
            if semantic(fact) not in allowed: raise Stop('derived fact not backed by paid response: '+rid)
            checked=p.checked_facts(record,{'partition_key':g['partition_key'],'facts':[raw_fact(fact)]})
            errors=checked['rejected']
            # Only the new subject/type/description rules may invalidate a formerly accepted fact.
            if errors and any(e['code'] not in ('E_SUBJECT_OBJECT','E_SUBJECT','E_SUBJECT_TYPE','E_SUBJECT_DESCRIPTION') for r in errors for e in r['errors']):
                raise Stop('non-subject validation drift: '+rid)
        generated[rid]=g
    staged=home/'state.importing'
    if staged.exists(): raise Stop('incomplete import staging; inspect first')
    staged.mkdir(parents=True)
    ledger=Ledger(staged/'paid.jsonl',digest(plan),plan['caps'],subscription_limits=plan.get('subscription_limits'))
    # All historical charges remain; old calls cannot cache-hit new DB-dependent work.
    for row in latest.values():
        new_id=digest(['fresh-history',digest(old_plan),row['id']]);new={**row,'id':new_id}
        value=values.get(row.get('result'))
        account='subscription' if value and value.get('turns') else 'cash'
        if value is not None:
            new['result']='responses/'+new_id+'.json';save(staged/new['result'],value)
        new.update(account=account,model='Sol' if row['step']=='quality' else 'Luna' if account=='subscription' else None,
                   imported_from={'state':str(archive.relative_to(ROOT)), 'original_id':row['id'],'binding':digest(old_plan)})
        ledger.append(new)
    # Preserve released reservations even when the same old ID later succeeded.
    for i,row in enumerate(old.rows):
        if row['state']=='released' and latest[row['id']] is not row:
            ledger.append({**row,'id':digest(['fresh-release',digest(old_plan),i]),'account':'cash'})
    response_paths={r.get('result'):'responses/'+digest(['fresh-history',digest(old_plan),r['id']])+'.json'
                    for r in latest.values() if r.get('result')}
    for rid,g in generated.items():
        g={**g,'raw_response':response_paths.get(g.get('raw_response'),g.get('raw_response'))}
        save(staged/'generated'/f'{rid}.json',g)
    shutil.copytree(archive/'packets',staged/'packets')
    save(staged/'plan.json',plan)
    result={'records':len(generated),'paid_effects':len(latest),'database_imported':False,
            'archive':str(archive.relative_to(ROOT)), 'generated_sha':{rid:digest(g) for rid,g in generated.items()}}
    save(staged/'import.json',result)
    staged.rename(state)
    return result
