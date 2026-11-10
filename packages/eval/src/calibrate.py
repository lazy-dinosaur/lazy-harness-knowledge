"""Optional pure-heuristic calibration from saved labels and cached vectors only; never a resume prerequisite.
A blocked report is not an applied config. E5 unchanged defaults remain usable explicitly.
"""
import math
from collections import Counter
from safety import Stop, digest, load, save, RUNNER
from local_db import HOST
import embedding_models as models
import embedding_thresholds as thresholds


def metrics(scores, labels, cutoff):
    tp = sum(y and s>=cutoff for s,y in zip(scores,labels))
    fp = sum(not y and s>=cutoff for s,y in zip(scores,labels))
    positives = sum(labels)
    return {'precision':tp/(tp+fp) if tp+fp else 1.,'recall':tp/positives if positives else 0.,
            'tp':tp,'fp':fp,'positives':positives,'negatives':len(labels)-positives}


def fit(old_scores, new_scores, labels, old_cutoff):
    if len(old_scores)!=len(new_scores) or len(labels)!=len(new_scores) or any(type(y) is not bool for y in labels):
        raise ValueError('invalid calibration rows')
    if any(not math.isfinite(s) or not -1.000001<=s<=1.000001 for s in old_scores+new_scores):
        raise ValueError('invalid cosine scores')
    baseline = metrics(old_scores,labels,old_cutoff)
    if not baseline['positives'] or not baseline['negatives'] or not baseline['tp']:
        return {'status':'blocked','reason':'need positive/negative labels and at least one e5 true positive','e5':baseline,'e5_cutoff':old_cutoff}
    candidates = sorted(set([-1.,1.]+[max(-1.,min(1.,s)) for s in new_scores]))
    valid = [(metrics(new_scores,labels,t),t) for t in candidates]
    valid = [(m,t) for m,t in valid if m['recall']+1e-12>=baseline['recall'] and m['precision']+1e-12>=baseline['precision']]
    if not valid:
        return {'status':'blocked','reason':'no threshold preserves e5 recall at equal/better precision','e5':baseline,'e5_cutoff':old_cutoff}
    best,cutoff = max(valid,key=lambda item:(item[0]['precision'],item[0]['recall'],item[1]))
    return {'status':'fitted','threshold':cutoff,'e5_cutoff':old_cutoff,'e5':baseline,'active':best}


def cosine(left, right):
    if len(left)!=len(right) or not left:
        raise ValueError('vector dimensions differ')
    norm = math.sqrt(sum(v*v for v in left)*sum(v*v for v in right))
    if not norm:
        raise ValueError('zero calibration vector')
    return max(-1.,min(1.,sum(a*b for a,b in zip(left,right))/norm))


def inventory():
    anchors = [('subject_dict.py','def nearest','subject','top-K; no cosine floor'),
        ('domain_router.py','def nearest','domain','top-K; no cosine floor'),
        ('cleanup.py','def pairs','cleanup','top-K per fragment; no cosine floor'),
        ('cleanup.py',"a.source->>'possible_duplicate'",'cleanup_possible_duplicate','cross-domain eligibility; no cosine floor'),
        ('capture_audit.py','DUP_COSINE =','capture_dedupe','cosine'),('rule_cli.py','SELECT_MIN =','rule_select','cosine'),
        ('store_pg.py','def search(','retrieval','top-k/RRF; no default cosine cutoff'),
        ('subject_dict.py','SAME_KEEP =','subject same','Jev probability; unchanged'),
        ('domain_router.py','SAME_MIN =','domain same','Jev probability; unchanged'),
        ('domain_router.py','ROUTE_MIN =','domain route','Jev probability; unchanged'),
        ('domain_router.py','NEW_MIN =','domain new','Jev probability; unchanged'),
        ('runner.py','DUP_SKIP_MAX =','digest/worktime duplicate','Jev probability; unchanged'),
        ('digest_driver.py','worktime_driver.existing_excerpt','digest retrieval','delegates to store_pg; no separate cosine cutoff'),
        ('worktime_driver.py','store_pg.search','worktime retrieval','delegates to store_pg; no separate cosine cutoff')]
    rows=[]
    for path,anchor,name,kind in anchors:
        lines=(RUNNER/path).read_text().splitlines()
        matches=[i for i,line in enumerate(lines,1) if anchor in line]
        rows.append({'file':path,'lines':matches,'name':name,'kind':kind,'e5':thresholds.E5.get(name)})
    return rows


def surfaces(pairs, family):
    import fact_text
    group = 'fragment' if family in ('cleanup','cleanup_possible_duplicate','capture_dedupe') else family
    out=[]
    for row in pairs:
        if row['family']!=group: continue
        r=dict(row)
        if group=='fragment':
            r['roles']=['passage','passage']
            if family!='capture_dedupe':
                r['left'],r['right']=fact_text.view(r['left']),fact_text.view(r['right'])
        out.append(r)
    return out


def assert_applied(plan, home):
    import embed
    value=thresholds.read_profile(embed.MODEL_ID,home/'state/embedding-thresholds.json')
    if value.get('binding')!=digest(plan):
        raise Stop('calibration plan binding drift')
    report = load(home/'state/calibration.json')
    evidence = load(home/'state/calibration-pairs.json')
    if report.get('status') != 'applied' or digest(report) != value.get('report_sha') or evidence.get('binding') != digest(plan) or digest(evidence.get('pairs')) != value.get('evidence_sha'):
        raise Stop('calibration report/evidence drift')
    return value


def run(plan, home, paid, dsn):
    import embed
    import store_pg as pg
    import precompute
    from reembed import local_only
    from calibration_evidence import collect
    local_only(dsn)
    if embed.MODEL_ID!=embed.REMOTE_MODEL_ID:
        raise Stop('calibration requires the selected remote model')
    with pg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute('select host_id from knowledge.host order by host_id')
        if cur.fetchall()!=[(HOST,)]: raise Stop('foreign calibration host')
        pairs,audit=collect(cur,home,paid)
    save(home/'state/calibration-pairs.json',{'binding':digest(plan),'pairs':pairs,'audit':audit})
    report={'binding':digest(plan),'model_id':embed.MODEL_ID,'evidence_sha':digest(pairs),'audit':audit,
        'inventory':inventory(),'table':{},'evaluation':'same paid labeled pairs; fitted, NOT held-out quality evidence',
        'label_semantics':'record-need no-new-content/new, intent unchanged/changed, evidence-backed subject/domain same/different',
        'budget_usd':.009}
    target={name:surfaces(pairs,name) for name in thresholds.E5 if name not in ('retrieval','rule_select')}
    # Missing labels are a data problem, not a reason to spend money or inherit an e5 cutoff.
    for name,rows in target.items():
        counts=Counter(r['same'] for r in rows)
        if not counts[True] or not counts[False]:
            report['table'][name]={'status':'blocked','reason':'missing judged positive/negative pairs','positives':counts[True],'negatives':counts[False]}
    report['table']['rule_select']={'status':'blocked','reason':'fact/merge labels do not establish rule relevance; not used by this pilot'}
    report['table']['retrieval']={'status':'unchanged','threshold':None,'reason':'top-k/RRF has no default cosine cutoff'}
    if embed.REMOTE_MODEL!=models.E5 and all(name in report['table'] for name in target):
        report['status']='blocked';save(home/'state/calibration.json',report)
        raise Stop('optional heuristic calibration lacks labeled pairs; absorption does not require calibration')
    if embed.REMOTE_MODEL==models.E5:
        chosen=dict(thresholds.E5)
        report['table']={name:{'status':'unchanged','threshold':number,'reason':'explicit e5 selection; existing policy'} for name,number in chosen.items()}
    else:
        selected={name:rows for name,rows in target.items() if name not in report['table']}
        values={role:list(dict.fromkeys(row[side] for rows in selected.values() for row in rows
            for side,r in zip(('left','right'),row['roles']) if r==role)) for role in ('query','passage')}
        _,_,scopes=precompute.inventory(plan,home)
        budget_path=home/'state/calibration-budget.json'
        budget=load(budget_path) if budget_path.exists() else {'binding':digest(plan),'cap':.009,'spent_before':paid.ledger.spent('embedding')}
        if budget.get('binding')!=digest(plan) or budget.get('cap')!=.009:
            raise Stop('calibration budget binding drift')
        save(budget_path,budget)
        paid.embedding_budget=budget
        paid.scope='calibrate-v1:'+digest(pairs)
        try:
            for model in (models.E5,embed.REMOTE_MODEL):
                precompute.seed_paid(paid,values,scopes+[paid.scope],model=model)
                missing = sum(embed.CACHE.get(model+'@openrouter',role,text) is None
                              for role,texts in values.items() for text in texts)
                if missing:
                    raise Stop('optional calibration requires cached vectors; no paid embedding calls allowed')
            chosen={'retrieval':None}
            for name,rows in selected.items():
                scores={}
                for model in (models.E5,embed.REMOTE_MODEL):
                    scores[model]=[cosine(embed.CACHE.get(model+'@openrouter',r['roles'][0],r['left']),
                        embed.CACHE.get(model+'@openrouter',r['roles'][1],r['right'])) for r in rows]
                fitted=fit(scores[models.E5],scores[embed.REMOTE_MODEL],[r['same'] for r in rows],thresholds.E5[name])
                report['table'][name]=fitted
                if fitted['status']=='fitted': chosen[name]=fitted['threshold']
            report['embedding_charge_usd']=paid.ledger.spent('embedding')-budget['spent_before']
        finally:
            paid.embedding_budget=None
    missing=[name for name in target if name not in chosen]
    report['status']='blocked' if missing else 'applied'
    save(home/'state/calibration.json',report)
    if missing: raise Stop('calibration precision/recall gate failed: '+','.join(missing))
    config={'version':1,'binding':digest(plan),'model_id':embed.MODEL_ID,'status':'applied','thresholds':chosen,
        'evidence_sha':digest(pairs),'report_sha':digest(report)}
    config['sha256']=thresholds.fingerprint(config)
    save(home/'state/embedding-thresholds.json',config)
    return report
