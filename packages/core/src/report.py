"""Offline usage report: only terminal paid receipts, all turns, no last-turn estimate."""
import json
from safety import HERE, RUNNER, load, save
import sys
sys.path.insert(0, str(RUNNER))
from prepare import scope_home

def report(home=None):
    home=home or HERE
    path=home/'state/paid.jsonl'
    rows=[json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    latest={r['id']:r for r in rows}; out={}
    providers={}
    for row in latest.values():
        if row.get('imported_from'): continue
        provider=row.get('model') if row.get('account')=='subscription' else 'embedding' if row['step']=='embedding' else 'Jev'
        key=row['step']+'/'+str(provider or 'Luna')
        bucket=providers.setdefault(key,{'calls':0,'seconds':0.,'cash_usd':0.})
        bucket['calls']+=1; bucket['seconds']+=row.get('seconds',0)
        if row.get('account')!='subscription': bucket['cash_usd']+=row['charge']
    cash=0.; subscription={'equivalent_usd':0.,'calls':0,'seconds':0.,'by_model':{}}
    # Retain released charges even when an old key was subsequently retried.
    accounting=list(latest.values())+[r for r in rows if r['state']=='released' and latest[r['id']] is not r]
    for row in accounting:
        value=load(path.parent/row['result']) if row.get('result') else {}
        if row.get('account')=='subscription' or value.get('turns'):
            seconds=row.get('seconds',900 if row['state']=='reserved' else 0)
            subscription['equivalent_usd']+=row['charge'];subscription['calls']+=1;subscription['seconds']+=seconds
            model=row.get('model') or ('Sol' if row['step']=='quality' else 'Luna')
            usage=subscription['by_model'].setdefault(model,{'calls':0,'seconds':0.,'equivalent_usd':0.})
            usage['calls']+=1;usage['seconds']+=seconds;usage['equivalent_usd']+=row['charge']
        else: cash+=row['charge']
    for row in latest.values():
        group=out.setdefault(row['step'],{'requests':0,'actual_known_usd':0.,'charged_usd':0.,'unknown_cost_requests':0,
                                         'uncertain_requests':0,'seconds':0.,'tokens':{},'model_turns':0,
                                         'calls':0,'known_cost_calls':0,'max_charged_usd_per_call':0.,'max_actual_usd_per_call':None})
        group['calls']+=1
        group['max_charged_usd_per_call']=max(group['max_charged_usd_per_call'],row['charge'])
        group['requests']+=1;group['charged_usd']+=row['charge'];group['seconds']+=row.get('seconds',0)
        if row['state']!='done':
            if row['state']=='reserved': group['uncertain_requests']+=1
            else: group['released_reservations']=group.get('released_reservations',0)+1
            continue
        if row['cost_known']:
            group['actual_known_usd']+=row['charge']; group['known_cost_calls']+=1
            group['max_actual_usd_per_call']=max(group['max_actual_usd_per_call'] or 0.,row['charge'])
        else:group['unknown_cost_requests']+=1
        value=load(path.parent/row['result'])
        usages=value.get('turns') or [value.get('usage') or {}]
        if value.get('turns'):group['model_turns']+=len(usages)
        for usage in usages:
            usage={**usage, **(usage.get('prompt_tokens_details') or {})}
            for name,aliases in {'input':['input','input_tokens','prompt_tokens'],'output':['output','output_tokens','completion_tokens'],
                                 'cache_read':['cacheRead','cache_read_input_tokens','cached_tokens'],
                                 'cache_write':['cacheWrite','cache_creation_input_tokens']}.items():
                number=next((usage[a] for a in aliases if isinstance(usage.get(a),(int,float))),None)
                if number is not None:group['tokens'][name]=group['tokens'].get(name,0)+number
    for group in out.values():
        group['mean_charged_usd_per_call']=group['charged_usd']/group['calls']
        group['mean_actual_usd_per_call']=group['actual_known_usd']/group['known_cost_calls'] if group['known_cost_calls'] else None
        group['cache_info']={k:group['tokens'].get(k) for k in ('cache_read','cache_write')}
    from pipeline import generation_report
    generation=generation_report(load(home/'plan.json'),home) if (home/'plan.json').exists() else None
    failures=load(home/'state/maintenance-failures.json') if (home/'state/maintenance-failures.json').exists() else {}
    transition={name:load(home/'state'/(name+'.json')) for name in ('calibration','reembed','embedding-thresholds') if (home/'state'/(name+'.json')).exists()}
    result={'embedding_transition':transition,'steps':out,'generation':generation,'maintenance_failures':list(failures.values()),
            'embedding_cache':load(home/'state/embed-absorb-stats.json') if (home/'state/embed-absorb-stats.json').exists() else None,
            'embedding_precompute':load(home/'state/embed-precompute.json') if (home/'state/embed-precompute.json').exists() else None,
            'reservation_resolutions':[r for r in rows if r.get('resolution')],
            'description_failed':sum(r['status']=='description_failed' for r in failures.values()),
            'charged_usd':cash, 'cash_usd':cash, 'subscription_usage':subscription,
            'accounting_note':'Luna/Sol USD is token-equivalent usage, not cash; per-step legacy amounts may mix accounts.',
            'warning':'Unknown provider costs retain reservations, not zero. Token fields absent from receipts are unknown. Caps are admission limits; an in-flight provider call can overrun its reservation and halts subsequent execution.'}
    result['providers_by_step']=providers
    if (home/'plan.json').exists() and load(home/'plan.json').get('write_path'):
        from place_write_absorb import unit_report
        result['unit_authoring']=unit_report(load(home/'plan.json'),home)
        save(home/'state/unit-authoring-report.json',result['unit_authoring'])
    elif (home/'plan.json').exists() and load(home/'plan.json').get('area_catalog'):
        from area_report import acceptance
        readback=load(home/'state/readback.json') if (home/'state/readback.json').exists() else {}
        shorten=load(home/'state/subject-shorten-report.json') if (home/'state/subject-shorten-report.json').exists() else {}
        grounding=load(home/'state/subject-ground-report.json') if (home/'state/subject-ground-report.json').exists() else {}
        result['area_acceptance']=acceptance(readback,result,shorten,grounding)
        save(home/'state/area-acceptance.json',result['area_acceptance'])
    save(home/'state/cost-report.json',result);return result

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope',choices=['full','pilot','pilot-fresh','pilot-feature','pilot-area-10','pilot-area'],default='full')
    print(json.dumps(report(scope_home(parser.parse_args().scope)),ensure_ascii=False,indent=2))
