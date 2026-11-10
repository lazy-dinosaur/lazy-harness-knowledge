"""Unit-first placement -> existing guarded vector/Jev absorption and reports."""
import copy
import sys
from typing import Callable
from safety import RUNNER, Stop, digest, load, save
sys.path.insert(0, str(RUNNER))
from place_write import assert_completed, identity_text, safe_aliases, _norm
from subject_contract import unit_first_metadata


@unit_first_metadata()
def record_unit(unit: str, fact: dict, record: Callable, host: str, check: Callable):
    """No cross-domain link input; fixed identity is a trusted server option."""
    return record({'host_id':host, 'work_unit_id':unit, 'partition_key':fact['domain'], 'facts':[fact]},
                  assigned_area=fact['domain'], isolate_invalid=True, grounding_check=check,
                  fixed_subject=True, leaf_references=False, trusted_identity_refs=[copy.deepcopy(fact['identity_ref'])])


class IdentityHeld(ValueError): pass


@unit_first_metadata()
def install(plan, home, dsn, host, completion, encode):
    import store_pg as pg, subject_dict as sd, domain_router as dr, embed
    from area_catalog import install as seeds
    from identity_ref import checked, resolve, add_spelling, IdentityError
    active = [r for r in completion['units'] if r.get('fact') and not r.get('held_reasons')]
    # Reject spoofed refs before seeds, embeddings or any DB write.
    for row in active:
        ref=checked(row['subject']['chosen']['identity_ref'],host,row['domain']['chosen']['name'])
        if row['fact'].get('identity_ref') != ref or row['fact'].get('subject_id') != ref['subject_id'] or row['fact']['domain'] != ref['domain']:
            raise IdentityError('E_IDENTITY: completion identity drift')
    # Persisted/allocated UUID ownership is read-validated before even domain seeds.
    with pg.connect(dsn) as conn,conn.cursor() as cur:
        for row in active:
            subject=row['subject']['chosen']; ref=subject['identity_ref']
            if subject.get('identity_origin') == 'allocated':
                cur.execute('select host_id,domain,name from knowledge.subject where subject_id=%s',(ref['subject_id'],))
                owner=cur.fetchone()
                if owner and owner != (host,ref['domain'],subject['name']):
                    raise IdentityError('E_IDENTITY: allocated UUID already owned')
            else:
                obj=resolve(cur,ref,host,ref['domain'])
                if obj['subject_id']==ref['subject_id'] and obj['name']!=subject['name']:
                    raise IdentityError('E_IDENTITY: selected display name changed')
    seeds(plan,home,dsn,host,encode)
    # Preflight against final names too: a later canonical name cannot be stolen by an earlier alias.
    domains=list({r['domain']['chosen']['name']:r['domain']['chosen'] for r in active}.values())
    subjects={}
    for r in active:
        subjects.setdefault(r['domain']['chosen']['name'],[]).append(r['subject']['chosen'])
    held={}; traces={}
    for row in active:
        domain = row['domain']['chosen']; subject = row['subject']['chosen']
        trace={}; traces[row['unit_id']]=trace
        try:
            for chosen,catalog in ((domain,domains),(subject,subjects[domain['name']])):
                safe_aliases(chosen,chosen.get('aliases',[]),catalog,trace)
            # sd.add_alias expands spelling tails. Protect future canonical names at that same boundary.
            for alias in subject.get('aliases',[]):
                keys={sd.norm(alias),sd.core(alias)}
                owners=[r['name'] for r in subjects[domain['name']] if r['name']!=subject['name'] and
                    any(keys & {sd.norm(n),sd.core(n)} for n in [r['name'],*r.get('aliases',[])])]
                if owners: trace.setdefault('skipped_aliases',[]).append({'alias':alias,'owners':owners,'reason':'store spelling/core collision'})
            if trace.get('skipped_aliases'): raise IdentityHeld('alias collision before materialization')
            vectors = encode([identity_text('domain',domain),identity_text('subject',subject)])
            with pg.connect(dsn) as conn, conn.cursor() as cur:
                domain_receipt=row['domain'].get('sdk_domain_review')
                if domain.get('area_evidence'):
                    dr.ensure_reviewed(cur,host,domain,domain_receipt)
                else:
                    dr.ensure(cur,host,domain['name'],domain['description'])
                cur.execute('''insert into knowledge.domain_embedding(host_id,domain,model_id,embedding)
                    values(%s,%s,%s,%s::extensions.vector) on conflict(host_id,domain,model_id)
                    do update set embedding=excluded.embedding''',(host,domain['name'],embed.MODEL_ID,sd._vec(vectors[0])))
                cur.execute('select alias,domain from knowledge.domain_alias where host_id=%s',(host,))
                persisted=cur.fetchall()
                cur.execute('select domain from knowledge.domain_type where host_id=%s',(host,))
                persisted.extend((name,name) for name, in cur.fetchall())
                for alias in domain.get('aliases',[]):
                    if any(_norm(a)==_norm(alias) and owner!=domain['name'] for a,owner in persisted):
                        trace.setdefault('skipped_aliases',[]).append({'alias':alias,'reason':'persisted domain alias collision'})
                        raise IdentityHeld('domain alias placement collision')
                    cur.execute('insert into knowledge.domain_alias(host_id,alias,domain) values(%s,%s,%s) on conflict do nothing',(host,alias,domain['name']))
                ref = subject['identity_ref']
                if subject.get('identity_origin') == 'allocated':
                    sid = sd.register(cur,host,subject['name'],vectors[1],domain=domain['name'],description=subject['description'],subject_id=ref['subject_id'])
                else:
                    sid = resolve(cur,ref,host,domain['name'])['subject_id']
                effective_ref = {**ref,'subject_id':sid}
                for alias in subject.get('aliases',[]):
                    add_spelling(cur,effective_ref,alias)
                cur.execute('''insert into knowledge.subject_embedding(subject_id,model_id,embedding)
                    values(%s,%s,%s::extensions.vector) on conflict(subject_id,model_id)
                    do update set embedding=excluded.embedding''',(sid,embed.MODEL_ID,sd._vec(vectors[1])))
                if resolve(cur,ref,host,domain['name'])['subject_id'] != sid:
                    raise IdentityHeld('fixed placement UUID changed before record')
        except (IdentityHeld,IdentityError) as exc:
            held[row['unit_id']]={'status':'held','errors':[{'code':'E_IDENTITY','where':'install','detail':str(exc)}],
                                  'unit_id':row['unit_id'],'input_sha':digest(row['fact']),'trace':trace}
            save(home/'state/fact-dispositions'/(row['unit_id']+'.json'),held[row['unit_id']])
    save(home/'state/identity-install.json',{'held':held,'traces':traces})
    return held


@unit_first_metadata()
def absorb(plan, home, dsn, paid, cfg, host):
    import generation as p, store_pg as pg, knowledge_cli as cli, digest_driver as dd, worktime_driver as wd, embed
    from precompute import assert_completed as precomputed
    completion = assert_completed(plan,home)  # before any DB effect
    precomputed(plan,home)
    identity_held=install(plan,home,dsn,host,completion,embed.encode_queries)
    before = embed.CACHE.stats()
    adapt, _ = p.adapters(cfg,paid)
    from active_judgements import make_actor_judge
    active_judge = make_actor_judge(cfg,paid=paid)
    for row in completion['units']:
        if row.get('held_reasons') or not row.get('fact') or row['unit_id'] in identity_held: continue
        unit = row['unit_id']; fact = row['fact']; paid.scope = unit
        path = home/'state/fact-dispositions'/(unit+'.json')
        with pg.connect(dsn) as conn,conn.cursor() as cur:
            cur.execute('select entry_id::text,judgement_body from knowledge.ledger_entry where work_unit_id=%s',(unit,))
            entries = cur.fetchall()
        identity = {key:copy.deepcopy(fact.get(key)) for key in ('fact','subject','domain','aliases','evidence_refs','identity_ref','subject_id','source_unit')}
        if entries:
            if len(entries) != 1: raise Stop('unit ledger identity drift')
            body = entries[0][1]
            saved = body.get('area_validation')
            if not saved or len(body.get('facts',[])) != 1 or not all(body['facts'][0].get(k)==v for k,v in identity.items()):
                raise Stop('unit checkpoint lacks exact written fact')
            result = {**saved,'entry_id':entries[0][0]}
        else:
            def check(index, candidate):
                return [] if index == 0 and all(candidate.get(k)==v for k,v in identity.items()) else [{'code':'E_GROUNDING_DRIFT','where':'fact','detail':'unit-first sentence/identity/source drift'}]
            result = record_unit(unit,fact,lambda data,**kw:cli.record(dsn,data,same=None,embed_fn=adapt['embed_fn'],**kw),host,check)
        save(path,{**result,'input_sha':digest(fact),'unit_id':unit})
        if result.get('errors') or not result.get('entry_id'): continue
        entry = result['entry_id']
        if p.absorbed_unit(dsn,unit,1):
            pg.embed_unit(dsn,unit)
            continue
        wd.run_worktime(dsn,1,active_judge,entry_ids=[entry],semantic_grounding=True)
        with pg.connect(dsn) as conn,conn.cursor() as cur:
            cur.execute('select status::text from knowledge.work_unit where work_unit_id=%s',(unit,))
            status = cur.fetchone()[0]
        if status == 'active': pg.complete(dsn,unit)
        import domain_router as dr
        parts = dr.route_pending(dsn,unit,adapt['choose'],adapt['confirm'],adapt['embed_fn'])
        if any(domain != fact['domain'] for _,domain in parts): raise Stop('fixed unit domain changed during absorption')
        dd.run_digestion(dsn,unit,active_judge,domain=fact['domain'],maintenance=None,workers=1,
                         same=adapt['same'],embed_fn=adapt['embed_fn'])
    result = p.maintain(plan,dsn,paid,cfg,checkpoint='domain-batch')
    # ctx-v2 remains a view of current row identity, not a canonical prefix.
    pg.backfill_embeddings(dsn,host,variant='ctx-v2')
    result = p.readback(plan,dsn)
    result['embedding_cache'] = {key:embed.CACHE.stats()[key]-before[key] for key in before}
    save(home/'state/embed-absorb-stats.json',result['embedding_cache'])
    save(home/'state/unit-authoring-report.json',unit_report(plan,home))
    return result


def unit_report(plan, home):
    path = home/'state/place-write-complete.json'
    if not path.exists(): return {'status':'not-grounded','units':[]}
    complete = assert_completed(plan,home)
    rows = copy.deepcopy(complete['units'])
    readback = load(home/'state/readback.json') if (home/'state/readback.json').exists() else {}
    terminal = {entry[0]:record for record in readback.get('records',[]) for entry in record.get('entries',[])}
    for row in rows:
        stored = home/'state/fact-dispositions'/(row['unit_id']+'.json')
        row['store'] = load(stored) if stored.exists() else {'status':'held' if row.get('held_reasons') else 'not-run'}
        if any(e.get('code')=='E_IDENTITY' for e in row['store'].get('errors',[])):
            row.setdefault('held_reasons',[]).extend(row['store']['errors'])
        entry = row['store'].get('entry_id')
        row['absorption'] = [f for f in terminal.get(entry,{}).get('facts',[]) if f.get('entry_id') == entry]
    counts = {}; documents = {}; held = {}; subjects = set()
    for row in rows:
        chosen = row.get('subject',{}).get('chosen')
        if chosen:
            key = (row['domain']['chosen']['name'],chosen['name']); subjects.add(key)
            if row.get('fact') and not row.get('held_reasons'):
                counts[key] = counts.get(key,0)+1
                documents.setdefault(key,set()).add(row['record_id'])
        for reason in row.get('held_reasons',[]):
            key = reason.get('code','unknown')+': '+reason.get('detail','')
            held[key] = held.get(key,0)+1
    metrics = {'domains_created':len(complete['new_domains']),'subjects_count':len(subjects),
        'facts_per_subject':{'mean':sum(counts.values())/len(subjects) if subjects else 0.,
                             'max':max(counts.values(),default=0),
                             'counts':[{'domain':d,'subject':s,'facts':counts.get((d,s),0)} for d,s in sorted(subjects)]},
        'singleton_subjects':sum(v==1 for v in counts.values()),
        'cross_document_subject_reuse':sum(len(docs)>1 for docs in documents.values()),'held_units_by_reason':held}
    return {'status':'grounded','units':rows,'metrics':metrics,'new_domains':complete['new_domains'],
            'judged_units':len(rows),'held_units':sum(bool(r.get('held_reasons')) for r in rows),
            'written_units':sum('written_sentence' in r for r in rows),
            'domain_reuse':sum(not r['domain']['new'] for r in rows if r.get('domain',{}).get('chosen')),
            'subject_reuse':sum(not r['subject']['new'] for r in rows if r.get('subject',{}).get('chosen')),
            'readback_counts':readback.get('counts',{}),'quality_note':'offline readiness is not paid semantic quality acceptance'}
