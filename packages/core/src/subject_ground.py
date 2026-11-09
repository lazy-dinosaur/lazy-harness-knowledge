"""Source identity check -> one subscription rewrite of failing facts -> recheck.
Immutable inputs/results retain original indices and isolate holds; no DB access.
"""
import copy
import hashlib
import re
from collections import Counter
from safety import load, save, digest, Stop
from source_grounding import check_many, excerpt, remove_parent, repair_citations, reject_reintroduced, VERSION


def merge_rewrite(original, patch):
    """Luna may return a delta (R36 ranges only, R22 fact+ranges without reason)."""
    merged = copy.deepcopy(original)
    merged.update(copy.deepcopy(patch))
    merged.pop('index', None)
    if 'source_ranges' not in merged:
        merged['source_ranges'] = []
        for ref in merged.get('evidence_refs', []):
            match = re.search(r'#L(\d+)(?:-L(\d+))?', str(ref.get('locator', '')))
            if match: merged['source_ranges'].append([int(match[1]), int(match[2] or match[1])])
    # Derived fields must be rebuilt by checked_facts, never inherited as authority.
    merged.pop('form', None)
    return merged



def run(plan, home, paid, ask):
    import pipeline as p
    from subject_shorten import assert_completed as shortened
    from area_catalog import relabel
    if not plan.get('subject_ground'): raise Stop('subject-ground not enabled')
    if plan.get('grounding_version') != VERSION: raise Stop('grounding v5 requires fresh prepare/import from latest pilot-feature')
    root = home/'state/subject-ground'
    frozen_path = root/'input.json'
    if frozen_path.exists():
        frozen = load(frozen_path)
        if frozen['binding'] != digest(plan): raise Stop('grounding plan drift')
    else:
        shortened(plan, home)
        frozen = {'binding':digest(plan), 'records':{r['record_id']:load(home/'state/generated'/(r['record_id']+'.json')) for r in plan['records']}}
        save(frozen_path, frozen)
    reports = {}; outputs = {}
    for r in plan['records']:
        rid = r['record_id']; original = frozen['records'][rid]
        source = (p.source_home(home)/'v1'/r['path']).read_text()
        if hashlib.sha256(source.encode()).hexdigest() != r['sha256']: raise Stop('grounding source drift')
        path = root/(rid+'.json')
        binding = digest([plan, r, original])
        if path.exists():
            receipt = load(path)
            if receipt['binding'] != binding or receipt.get('version') != VERSION: raise Stop('grounding receipt drift')
        else:
            paid.scope = 'subject-ground:'+rid
            def cached_ask(state, texts, question):
                packet = [state, texts, question]
                key = digest(packet)
                path = root/'judgements'/rid/(key+'.json')
                if path.exists():
                    saved = load(path)
                    if saved['input'] != packet or saved['scores_sha'] != digest(saved['scores']):
                        raise Stop('grounding judgement drift')
                    return saved['scores']
                scores = ask(state, texts, question)  # production HTTP is journaled by Paid
                save(path, {'input':packet, 'scores':scores, 'scores_sha':digest(scores)})
                return scores
            name_cache = {}  # per distinct source/name, shared across initial and rewrite
            initial = check_many(original['facts'], source, cached_ask, name_cache=name_cache)
            # One source document owns this rejection set. A sibling's permissive
            # cached verdict cannot reseed a name rejected elsewhere in this document.
            from source_grounding import rejected_names
            denied = rejected_names(initial)
            for f, result in zip(original['facts'], initial):
                reject_reintroduced(f, result, result, denied_names=denied)
            failed = [i for i, result in enumerate(initial) if result['errors']]
            out = copy.deepcopy(original); final = copy.deepcopy(initial); rewritten = []
            attempts = {}
            for f, result in zip(out['facts'], initial):
                remove_parent(f, result)
                repair_citations(f, result, source)
            final = copy.deepcopy(initial)
            if failed:
                packet = {'grounding_version':VERSION, 'source':source, 'facts':[{'index':i, 'source_excerpt':excerpt(source, original['facts'][i]),
                          'subject_to_keep':original['facts'][i].get('subject'),
                          'rejected_sentence':original['facts'][i].get('fact'),
                          'original_fields_not_evidence':{k:out['facts'][i][k] for k in
                              ('subject','aliases','subject_type','subject_description','parent_subject','kind','reason') if k in out['facts'][i]},
                          'errors':initial[i]['errors']} for i in failed]}
                rewrite_path = root/(rid+'-rewrite.json')
                if rewrite_path.exists():
                    rewrite = load(rewrite_path)
                    if rewrite['input_sha'] != digest(packet): raise Stop('grounding rewrite drift')
                else:
                    try:
                        answer, usage = p.packet_model(paid, 'subject-ground-'+rid, packet,
                            p.generation_task() + ' 이번 한 번만 facts의 index별 사실을 원문에서 재작성한다. '
                            '통과한 사실은 입력에 없으며 만지지 않는다. 현재 subject_to_keep는 변경 금지, parent는 선택. '
                            '문장에 주어만 그대로 넣고 영역은 넣지 않는다. 누출 이름/예시 복사 금지. '
                            'rejected_sentence는 교정 대상일 뿐 근거가 아니다. source만 근거로 삼는다. '
                            '각 항목에 index와 source_ranges(원문 1-based 줄 번호)를 포함. 바뀐 필드만 반환해도 된다. '
                            'original_fields_not_evidence는 보존할 필드이지 근거가 아니다. '
                            '각 문장에는 해당 subject_to_keep 이름을 그대로(verbatim) 반드시 포함한다. 별칭만 넣는 것도 금지. '
                            '주어 이름을 바꾸거나 생략하지 않는다. 주어 근거 실패는 그대로 보류하며 새 rename 흐름은 없다. '
                            'JSON {"facts":[...]}만.',
                            charge_step='generation')
                        rewrite = {'input_sha':digest(packet), 'answer':answer, 'usage':usage}
                    except ValueError as exc:
                        rewrite = {'input_sha':digest(packet), 'answer':{}, 'error':str(exc)}
                    save(rewrite_path, rewrite)
                items = rewrite['answer'].get('facts', [])
                if not isinstance(items, list): items = []
                replacements = {}
                for i in failed:
                    candidates = [x for x in items if isinstance(x, dict) and type(x.get('index')) is int and x['index'] == i]
                    if len(candidates) != 1:
                        errors = [{'code':'E_REWRITE_OUTPUT', 'where':'fact',
                                   'detail':'expected exactly one rewrite item for original index'}]
                    else:
                        merged = merge_rewrite(out['facts'][i], candidates[0])
                        subject = original['facts'][i]['subject']
                        errors = []
                        if merged.get('subject') != subject or subject not in str(merged.get('fact','')):
                            errors = [{'code':'E_SUBJECT','where':'subject',
                                       'detail':'rewrite must keep current subject verbatim: '+subject}]
                        if not errors:
                            validated = p.checked_facts(r, {'partition_key':original['partition_key'], 'facts':[merged]}, reuse_existing=False)
                            errors = [{**e, 'where':e.get('where','fact')} for x in validated['rejected'] for e in x['errors']]
                    if errors:
                        attempts[i] = {'errors':errors, 'names':[], 'parents_removed':[],
                                       'leaked_sentence_names':[],
                                       'sentence':{'status':'not-checked', 'score':None, 'text':candidates[0].get('fact', original['facts'][i].get('fact')) if candidates else original['facts'][i].get('fact')}}
                        final[i]['errors'] += errors
                        continue
                    f = validated['facts'][0]; f.pop('index', None)
                    replacements[i] = f
                checked = check_many(list(replacements.values()), source, cached_ask, name_cache=name_cache)
                denied.update(rejected_names(checked))
                for (i, f), result in zip(replacements.items(), checked):
                    reject_reintroduced(f, result, initial[i], denied_names=denied)
                    attempts[i] = result
                    remove_parent(f, result)
                    final[i] = result
                    if not result['errors']:
                        repair_citations(f, result, source)
                        # Retain the pre-rewrite references, including range-only rewrites.
                        old_refs = original['facts'][i]
                        history = f.setdefault('source_reference_history', [])
                        previous = {k:copy.deepcopy(old_refs[k]) for k in ('source_ranges','evidence_refs','evidence_quote') if k in old_refs}
                        if previous not in history: history.append(previous)
                        out['facts'][i] = f; rewritten.append(i)
            # Verify original quotes as well as newly produced source_ranges.
            relabel(r, out, p.source_home(home)/'v1')  # validate refs; classification is owned by import
            counts = {'pass':sum(not x['errors'] and all(n['status']=='pass' for n in x['names'] if not (n['where']=='parent_subject' and n['status']=='held')) for x in initial),
                      'jev-pass':sum(not x['errors'] and any(n['status']=='jev-pass' for n in x['names']) for x in initial),
                      'held':sum(bool(x['errors']) for x in final), 'rewritten':len(rewritten)}
            counts.update(sentence_pass=sum(x['sentence']['status']=='jev-pass' for x in initial),
                          sentence_fail=sum(x['sentence']['status']=='held' for x in initial),
                          rewrite_sentence_pass=sum(x['sentence']['status']=='jev-pass' for x in attempts.values()),
                          rewrite_sentence_fail=sum(x['sentence']['status']=='held' for x in attempts.values()),
                          parents_removed=sum(len(x['parents_removed']) for x in initial)+
                                          sum(len(x['parents_removed']) for x in attempts.values()))
            counts.update(rewrite_pass=len(rewritten), rewrite_fail=len(failed)-len(rewritten),
                          repaired_ranges=sum(x['sentence'].get('citation_status')=='repaired' for x in final),
                          citation_unlocated=sum(str(x['sentence'].get('citation_status','')).startswith('unlocated') for x in final))
            for prefix, values in (('',initial), ('rewrite_',list(attempts.values())), ('final_',final)):
                for question in ('q1','q2'):
                    counts[prefix+question+'_pass'] = sum(x.get(question,{}).get('status')=='jev-pass' for x in values)
                    counts[prefix+question+'_fail'] = sum(x.get(question,{}).get('status')=='held' for x in values)
                    counts[prefix+question+'_not_checked'] = sum(question not in x for x in values)
            subject_names = {}
            for prefix, values in (('initial',initial), ('rewrite',list(attempts.values())), ('final',final)):
                rows = {n['name']:n for x in values for n in x['names'] if n['where']=='subject'}
                subject_names[prefix] = list(rows.values())
                counts[('' if prefix=='initial' else prefix+'_')+'subject_name_pass'] = sum(n['status']!='held' for n in rows.values())
                counts[('' if prefix=='initial' else prefix+'_')+'subject_name_fail'] = sum(n['status']=='held' for n in rows.values())
            error_codes = dict(Counter(e['code'] for x in final for e in x['errors']))
            rewrite_error_codes = dict(Counter(e['code'] for x in attempts.values() for e in x['errors']))
            all_checks = list(enumerate(initial)) + list(attempts.items())
            receipt = {'subject_names':subject_names, 'q2_offending_names':sorted({n for _, x in all_checks for n in x.get('q2',{}).get('offending_names',[])}),
                       'rejected_names':sorted(denied),
                       'version':VERSION, 'binding':binding, 'input_sha':digest(original), 'output':out, 'checks':final,
                       'rewrite_checks':attempts, 'error_codes':error_codes,
                       'rewrite_error_codes':rewrite_error_codes,
                       'citation_statuses':{str(i):x['sentence'].get('citation_status','not-checked') for i,x in enumerate(final)},
                       'leaked_sentence_names':sorted({n for _, x in all_checks for n in x['leaked_sentence_names']}),
                       'sentence_failures':[{'index':i, 'stage':stage, **x['sentence']}
                           for stage, values in (('initial',enumerate(initial)),('rewrite',attempts.items()))
                           for i,x in values if x['sentence']['status']=='held'],
                       'initial':initial, 'counts':counts, 'rewritten_indices':rewritten,
                       'leaked_names':sorted({n['name'] for x in initial for n in x['names'] if n['status']=='held'})}
            save(path, receipt)  # receipt precedes apply: interrupted resumes never rewrite twice
        out = receipt['output']; target = home/'state/generated'/(rid+'.json')
        if load(target) not in (original, out): raise Stop('grounding generated drift')
        save(target, out); outputs[rid] = digest(out)
        reports[rid] = {k:receipt[k] for k in ('counts','leaked_names','leaked_sentence_names','sentence_failures','rewritten_indices',
                                                     'error_codes','rewrite_error_codes','citation_statuses','q2_offending_names','rejected_names','subject_names')}
    report = {'version':VERSION, 'binding':digest(plan), 'completed':True, 'generated':outputs, 'records':reports,
              'status':'completed_with_holds' if any(x['counts']['held'] for x in reports.values()) else 'completed'}
    save(home/'state/subject-ground-report.json', report)
    return report


def assert_completed(plan, home):
    if not plan.get('subject_ground'): return
    path = home/'state/subject-ground-report.json'
    if not path.exists(): raise Stop('subject-ground must complete before embed-precompute/absorb')
    report = load(path)
    actual = {r['record_id']:digest(load(home/'state/generated'/(r['record_id']+'.json'))) for r in plan['records']}
    if plan.get('grounding_version') != VERSION or report.get('version') != VERSION or report.get('binding') != digest(plan) or not report.get('completed') or report.get('generated') != actual:
        raise Stop('subject-ground completion drift')


def record_check(plan, home, record, generated):
    assert_completed(plan, home)
    receipt = load(home/'state/subject-ground'/(record['record_id']+'.json'))
    if receipt.get('version') != VERSION or receipt['output'] != generated: raise Stop('grounding output drift')
    def validate(index, fact):
        # Assigned area and normalized form may differ; identity and canonical text must not.
        for key in ('fact','subject','subject_type','subject_description','parent_subject','aliases','evidence_refs'):
            if fact.get(key) != generated['facts'][index].get(key): raise Stop('grounding fact drift: '+key)
        return receipt['checks'][index]['errors']
    return validate
