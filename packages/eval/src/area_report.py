"""Offline area acceptance projection; no model/DB calls."""
from collections import Counter, defaultdict

def acceptance(readback, usage, shorten=None, grounding=None):
    tables=readback.get('tables',{})
    domains=[d for d in tables.get('domain_type',[]) if d.get('status')=='active' and not d.get('merged_into')]
    subjects=[s for s in tables.get('subject',[]) if not s.get('merged_into')]
    documents=defaultdict(set); refs=Counter(); reasons=[]
    seen_subjects=set(); reused_subjects=0; created_by_document=0
    reviews=readback.get('review_list',[])
    queued={(r.get('entry_id'),r.get('fact_index')) for r in reviews if r.get('entry_id') is not None}
    for record in readback.get('records',[]):
        assigned={f['subject_id'] for f in record.get('facts',[]) if f.get('subject_id')}
        reused_subjects+=len(assigned & seen_subjects)
        created_by_document+=len(assigned - seen_subjects)
        seen_subjects.update(assigned)
        for f in record.get('facts',[]):
            if f.get('domain'): documents[f['domain']].add(record['path'])
            if f.get('subject_id'): refs[f['subject_id']]+=1
        if record.get('status')!='absorbed':
            record_entries={e[0] for e in record.get('entries',[])}
            pending=[r for r in reviews if r.get('entry_id') in record_entries]
            reasons.append({'record_id':record['record_id'],'status':record['status'],
                            'reason':'no-facts' if not record.get('expected_facts') else 'queued_for_human' if pending else 'missing disposition/vector/structure or failed fact',
                            'reviews':pending, 'held_facts':record.get('held_facts',[])})
    held = [{'record_id':r['record_id'], 'path':r['path'], **f}
            for r in readback.get('records',[]) for f in r.get('held_facts',[])]
    return {'q2_offending_names':sorted({n for r in (grounding or {}).get('records',{}).values() for n in r.get('q2_offending_names',[])}),
            'grounding':(grounding or {}).get('records',{}),
            'leaked_sentence_names':sorted({name for r in (grounding or {}).get('records',{}).values() for name in r.get('leaked_sentence_names',[])}),
            'grounding_status':(grounding or {}).get('status','not-run'),
            'leaked_names':sorted({name for r in (grounding or {}).get('records',{}).values() for name in r.get('leaked_names',[])}),
            'held_facts':held, 'held_fact_count':len(held),
            'ungrounded_rejections':[f for f in held if any(e['code'] in ('E_SUBJECT_UNGROUNDED','E_SENTENCE_UNGROUNDED','E_SENTENCE_NAMES_UNGROUNDED') for e in f['errors'])],
            'subject_name_length_distribution':dict(sorted(Counter(len(s['name'].split()) for s in subjects).items())),
            'alias_count':len(tables.get('subject_alias',[])),
            'short_name_failures':(shorten or {}).get('failures',[]),
            'short_name_status':(shorten or {}).get('status','not-run'),
            'reused_shorten_subscription_usage':(shorten or {}).get('reused_response_usage',[]),
            'domain_count':len(domains), 'domain_documents':{d['domain']:sorted(documents[d['domain']]) for d in domains},
            'subjects_per_domain':{d['domain']:[s['name'] for s in subjects if s['domain']==d['domain']] for d in domains},
            'created_subjects':len(tables.get('subject',[])), 'reused_subjects':reused_subjects,
            'first_used_subjects':created_by_document, 'reused_subject_references':sum(max(0,n-1) for n in refs.values()),
            'reuse_basis':'reused_subjects counts document-to-subject bindings seen in an earlier document; created_subjects counts all dictionary rows including merged/parents; not a semantic correctness claim',
            'parent_features':[{'domain':s['domain'],'name':s['name'],'parent_subject_id':s['parent_subject_id']} for s in subjects if s.get('parent_subject_id')],
            'counts':readback.get('counts',{}), 'queued_for_human':len(queued), 'reasons':reasons,
            'merge_counts':dict(Counter(('luna_called' if r.get('verdict',{}).get('luna_called') else 'jev_filtered') for r in tables.get('merge_judgement',[]))),
            'steps':usage.get('steps',{}),'cash_usd':usage.get('cash_usd'),
            'providers_by_step':usage.get('providers_by_step',{}),
            'subscription_usage':usage.get('subscription_usage',{}),
            'complete':readback.get('complete',False)}
