"""Offline inventory, corrected 64-question G14 baselines and size-based estimates."""
import hashlib
import json
import math
from pathlib import Path
from safety import HERE, RUNNER, digest, load, save
import sys
sys.path.insert(0, str(RUNNER))

EXCLUDED = {'R10','R15','R32','R49','R55','X-R32-R33'}
CAPS = {'generation':12., 'absorption':150., 'maintenance':100., 'quality':50.,
        'reading':40., 'judging':5., 'embedding':1.}

PILOT_CAPS = {'generation':.5, 'absorption':5., 'maintenance':4., 'quality':4.,
              'reading':10.5, 'judging':.8, 'embedding':.2}

from scopes import (SCOPES, AREA_SCOPES, TEN_SCOPES, PRIOR_REQUIRED_SCOPES, FRESH_SCOPES,
                    scope_home, source_home, default_runs)  # re-exported for experiment callers


def inputs(scope='full'):
    if scope not in SCOPES: raise ValueError('invalid scope')
    report = load(HERE/'restore-report.json')
    assert set(report['excluded_questions']) == EXCLUDED
    records = []
    for r in load(RUNNER/'scale-01/records.json'):
        if r['record_id'] in report['missing']: continue
        path = (HERE/'v1'/r['path']).resolve()
        assert path.is_relative_to(HERE/'v1')
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == r['sha256'], r['record_id']
        text = raw.decode()
        title = next((s.lstrip('# ').strip() for s in text.splitlines() if s.startswith('# ')), Path(r['path']).stem)
        records.append({**r,'chars':len(text),'lines':len(text.splitlines()),'title':title})
    qs = [q for q in load(RUNNER/'read-01/questions.json') if q['qid'] not in EXCLUDED]
    assert len(records)==598 and len(qs)==64
    flat = {c['rid']+':'+c['id']:c['claim'] for c in load(RUNNER/'capture-01/s1b-claims-flat.json')}
    verdict = {x['id']:x['verdict'] for n in (1,2,3) for x in load(RUNNER/f'scale-03/measure/sol-result-{n}.json')['items']}
    for q in qs:
        q['nuggets'] = [n for n in q['nuggets'] if verdict[n] != 'superseded']
        q['claims'] = [flat[n] for n in q['nuggets']]
        raw = (RUNNER/'read-01/b3-queries'/f"{q['qid']}.raw.txt").read_text()
        import sys
        sys.path.insert(0,str(RUNNER/'fragmenter'))
        from check_fragments import parse
        q['queries'] = parse(raw)['queries'][:6]
    if scope in SCOPES[1:]:
        records = [r for r in records if r['pilot']]
        assert len(records) == 55
        if scope in TEN_SCOPES:
            from area_catalog import PILOT_TEN
            records = [r for r in records if r['record_id'] in PILOT_TEN]
            assert len(records) == 10
    elif scope != 'full': raise ValueError('invalid scope')
    return records,qs

def baseline(qs):
    expected = {(q['qid'], n) for q in qs for n in q['nuggets']}
    out = {}
    for label, name in [('single','judged-measure.json'),('mapreduce','judged-measure-raw-mr.json')]:
        rows = load(RUNNER/'scale-03/measure'/name)
        runs = []
        for run in (1,2,3):
            rr = [r for r in rows if r['run']==run and (r['qid'],r['nugget']) in expected]
            assert len(rr)==len(expected) and {(r['qid'],r['nugget']) for r in rr}==expected
            runs.append({'run':run,'n':len(rr),'covered':sum(r['covered']>=.7 for r in rr),
                         'contradicted':sum(r['contradicted']>=.7 for r in rr)})
        rates = [r['covered']/r['n'] for r in runs]
        out[label] = {'runs':runs,'mean':sum(rates)/3,'range':[min(rates),max(rates)],
                      'source_sha256':hashlib.sha256((RUNNER/'scale-03/measure'/name).read_bytes()).hexdigest()}
    return out

def estimate(records,qs,runs=3):
    chars=sum(r['chars'] for r in records); lines=sum(r['lines'] for r in records)
    # Explicit planning assumptions, not measured model tariff or subject counts.
    facts=math.ceil(lines*.30); subjects=math.ceil(facts/6); domains=min(len(records),200)
    gen_in=chars/2+len(records)*2200; gen_out=facts*180
    n=len(qs)*4*runs
    refresh=max(1.,(1+len(records)/domains)/2)  # uniform arrival approximation; domain skew can greatly increase this
    calls={'generation':len(records),'absorption':facts*2+len(records)*4+subjects*2,
           'maintenance':math.ceil((subjects+domains+subjects*2)*refresh),'quality':math.ceil(min(120,len(records))/10)+domains+math.ceil(subjects*2/10),
           'reading':n,'judging':n*2,'embedding':facts*3+subjects*4+n*10}
    cost={'generation':(gen_in*.10+gen_out*.40)/1e6,'absorption':calls['absorption']*.001,
          'maintenance':calls['maintenance']*.003+subjects*2*.001,'quality':calls['quality']*.03,
          'reading':n*(.008+.030),'judging':calls['judging']*.001,'embedding':calls['embedding']*100*.02/1e6}
    return {'source_chars':chars,'source_bytes':sum(r['bytes'] for r in records),'source_lines':lines,
            'assumed_facts':facts,'assumed_subjects':subjects,'assumed_domains':domains,'maintenance_refresh_multiplier':refresh,
            'assumed_generation_tokens':{'input':gen_in,'output':gen_out},'calls':calls,
            'usd':cost,'total_usd':sum(cost.values()),
            'hours':{'generation':len(records)*45/3600,'absorption':calls['absorption']*1/3600,
                     'maintenance':calls['maintenance']*12/3600,'quality':calls['quality']*45/3600,
                     'reading':n*70/3600,'judging':calls['judging']*1/3600},
            'assumptions':'2 source chars/token; 0.30 facts/line; 180 output tokens/fact; 6 facts/subject; <=200 domains; generation $0.10/$0.40 per million input/output tokens (planning proxy, verify tariff); Jev $0.001/request (reserve $0.002); metadata/merge Luna $0.003/call; Sol $0.03/packet; single brief $0.008 ALL turns plus 30 Jev requests/question; e5 $0.02/M tokens. No cache discount. Normal per-unit actor maintenance retained so subsequent routing sees domain/subject descriptions and vectors. Uniform partition arrival refresh multiplier; concentrated domains can cost much more.'}

def prepare(scope='full'):
    home=scope_home(scope); caps=PILOT_CAPS if scope!='full' else CAPS
    # A separate namespace is not a fresh cash allocation. Never inspect an old ledger here.
    import os
    if scope in PRIOR_REQUIRED_SCOPES and (home/'state').exists():
        raise ValueError('immutable started isolated plan; preserve state and ledger')
    prior = None
    if scope in TEN_SCOPES:
        raw = os.environ.get('LH_PILOT_CASH_PRIOR_USD')
        if scope in PRIOR_REQUIRED_SCOPES and (raw is None or not raw.strip()):
            raise ValueError('explicit approved prior pilot cash required')
        try: prior = float(raw if raw is not None else '0')
        except (TypeError, ValueError): raise ValueError('invalid prior pilot cash') from None
        if not math.isfinite(prior) or not 0 <= prior < .5: raise ValueError('invalid prior pilot cash')
    records,qs=inputs(scope)
    source_files = list(HERE.glob('*.py')) + list(HERE.glob('*.mjs')) + list(RUNNER.glob('*.py')) + list((RUNNER/'migrations').glob('*.sql'))
    source_files += [RUNNER/'pi-extension/knowledge.ts',RUNNER/'fragmenter/prompt-v3.md',RUNNER/'fragmenter/check_fragments.py',
                     RUNNER/'subject-criteria-01/compare2.py',RUNNER/'subject-criteria-01/results2.json']
    hashes={str(p.relative_to(RUNNER)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(source_files)}
    plan={'version':1,'records':records,'questions':qs,'caps':caps,'code':hashes,
          'baseline':baseline(qs),'estimate':estimate(records,qs,default_runs(scope)),
          'one_run_variant':estimate(records,qs,1),'paid_approved':False}
    # Smaller paired screening uses all restored pilot records and only answer-supported questions.
    pilot=[r for r in records if r['pilot']]; ids={r['record_id'] for r in pilot}
    pq=[q for q in qs if all(n.split(':')[0] in ids for n in q['nuggets'])]
    plan['pilot_variant']={'records':len(pilot),'questions':len(pq),'estimate':estimate(pilot,pq,1),
                           'warning':'Different corpus; screening only, not full G16 acceptance. Requires separate approval/plan.'}
    if scope=='pilot':
        plan.update(scope='pilot', default_runs=1, container='lh-knowledge-local-pilot-local',
                    host='knowledge-local-pilot', quality_sample_records=55)
    if scope in FRESH_SCOPES:
        plan.update(scope=scope, default_runs=1, container='lh-knowledge-local-'+scope+'-local',
                    host='knowledge-local-'+scope, quality_sample_records=55,
                    maintenance_schedule='once per domain after all records; shared checkpoint, no duplicate final pass',
                    fresh_database=True, reuse='archive checkpoint as evidence; import-state --empty-db reuses paid cache, never restores absorbed checkpoint')
        plan['caps']={'generation':0.,'absorption':5.,'maintenance':4.,'quality':4.,'reading':10.5,'judging':.8,'embedding':.2}
        plan['cash_caps']={'providers':['Jev','embedding'],'steps':plan['caps'],'total_usd':24.5}
        plan['subscription_limits']={'Luna':{'calls':6000,'seconds':28800,'per_call_seconds':900},
                                     'Sol':{'calls':1000,'seconds':14400,'per_call_seconds':900}}
        import sys
        sys.path.insert(0,str(RUNNER))
        from subject_contract import SUBJECT_TYPES
        plan['subject_contract']={'version':3,'types':list(SUBJECT_TYPES),'name_max_chars':60,
                                  'description_max_chars':300,'migration':'0022_subject_parent',
                                  'max_depth':2,'parent_types':['기능','작업 흐름','외부 연동','화면']}
        plan['resume_mode']='continue-rebuilt-checkpoint' if scope=='pilot-feature' else 'empty-db-paid-cache'
        plan['embedding_precompute']=True
        if scope=='pilot-feature':
            plan['reuse']='continue verified hierarchy-v3 checkpoint; preserve absorbed units, paid ledger and generated facts'
        import embedding_transport as et
        plan['embedding_policy']=et.policy()
        from candidate_policy import policy
        plan['candidate_policy']=policy()
        if scope=='pilot-feature':
            plan['embedding_transition']={'version':1,'model_id':plan['embedding_policy']['model']['model_id'],
                'required_phases':['embed-precompute','reembed'],
                'required_thresholds':[], 'optional_calibration':'cached vectors only; pure heuristics fail closed without model-bound evidence',
                'vector_coverage':'all fragment variants/current revisions, all subjects including parents/merged, all domains; leaves reference subjects'}
        plan['fresh_estimate']={'cash_usd_range':[.3,2.], 'wall_hours_range':[2,6],
            'basis':'Main pilot: Jev about $0.00007/request, embedding about $0.001/1000 requests; generation reused, domain batching removes repeated maintenance. Not measured; hedge losers may be billed.',
            'subscription':'Luna/Sol token USD is equivalent usage, NOT cash. Calls and seconds are separate admission limits.'}
        plan['legacy_mixed_estimate']=plan['estimate']
        plan['estimate']=plan['fresh_estimate']
    if scope in AREA_SCOPES:
        if scope in TEN_SCOPES:
            plan['cash_caps'].update(total_usd=.50,prior_spent_usd=prior)
        plan['subject_contract']={'version':11,'area_words':1,'subject_words':[1,3],'aliases_satisfy_subject':False,
            'types':[],'max_depth':1,'description_max_chars':300,'description_lines':[1,2],
            'criterion':'DDD2','criterion_sha256':__import__('ddd_criterion').criterion_sha(),
            'local_schema':'drop subject_type NOT NULL/CHECK in owned unit-first pilot only'}
        plan.update(area_catalog=True, write_path=2, sequential_units=True, subject_shorten=False, subject_ground=False, grounding_version=6,
                    search_embedding_variant='ctx-v2',
                    required_phases=['judge','place','write','ground','embed-precompute','absorb','report'],
                    area_fact_isolation=True, resume_mode='unit-first', quality_sample_records=len(records),
                    reuse='from-scratch judge/place/write; confirmed domain seeds only; never import legacy generated identities')
        plan['fresh_estimate']={'cash_usd_range':[.04,.35], 'jev_usd_range':[.04,.34], 'embedding_usd_range':[.0005,.01],
            'basis':'188 units assumed; target/full-content placement, DDD retry, up to ten pair checks per new identity and Jev kind add variable calls. About 1000-4300 Jev requests at $0.00004-$0.00008, not measured. Embeddings pplx $0.004/1M tokens, retries/hedge may add cost.',
            'subscription':'Luna judge10 + per-unit alias/name and first-write calls + at most one rewrite/unit; subscription separate, not cash.'}
        plan['estimate']=plan['fresh_estimate']
    if (home/'plan.json').exists() and load(home/'plan.json')!=plan:
        if scope not in AREA_SCOPES or (home/'state').exists():
            raise ValueError('immutable plan differs; preserve old plan/ledger in separate approved experiment')
        # Offline-only unstarted area plans may be refreshed; preserve the old exact plan.
        old=load(home/'plan.json')
        save(home/(('plan-before-place-write-' if plan.get('write_path') else 'plan-before-short-names-')+digest(old)+'.json'),old)
    save(home/'plan.json',plan); save(home/'baselines-64.json',plan['baseline'])
    return {'records':len(records),'questions':len(qs),'nuggets':sum(len(q['nuggets']) for q in qs),
            'baseline':plan['baseline'],'estimate':plan['estimate'],'one_run':plan['fresh_estimate']['cash_usd_range'] if scope in FRESH_SCOPES else plan['one_run_variant']['total_usd'],
            'pilot_variant':plan['pilot_variant'],'caps':plan['caps'],'cash_caps':plan.get('cash_caps'),
            'subscription_limits':plan.get('subscription_limits'),'plan_sha':digest(plan)}

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope',choices=SCOPES,default='full')
    print(json.dumps(prepare(parser.parse_args().scope),ensure_ascii=False,indent=2))
