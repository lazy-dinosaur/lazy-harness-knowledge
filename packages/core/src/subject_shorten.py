"""One subscription Luna call per record, shared long-name mapping, immutable facts.
Frozen corpus + response receipts make partial application/crash resume deterministic.
No database, embeddings, or generation/refix calls in this phase.
"""
import copy
from collections import defaultdict
from safety import load, save, digest, Stop
from subject_contract import short_name_errors, hierarchy_errors, packed_name, SHORT_NAME_GUIDANCE
import runner


def objects(fact):
    yield {'name':fact.get('subject'), 'subject_type':fact.get('subject_type'),
           'description':fact.get('subject_description'), 'parent':fact.get('parent_subject')}
    parent = fact.get('parent_subject')
    if parent:
        yield {'name':parent.get('name'), 'subject_type':parent.get('subject_type'),
               'description':parent.get('description'), 'parent':None}


def needs_shortening(obj):
    return bool(short_name_errors(obj['name']) or runner.subject_error(obj['name']) or
                packed_name(obj['name'], parent=obj.get('parent')))


def apply_fact(original, mapping, hint):
    fact = copy.deepcopy(original)
    for target, field in ((fact, 'subject'), (fact.get('parent_subject'), 'name')):
        if not target: continue
        old = target[field]
        mapped = mapping.get(old, {})
        if mapped.get('status') != 'shortened': continue
        target[field] = mapped['name']
        target['aliases'] = list(dict.fromkeys([*target.get('aliases', []), old]))
    errors = runner.metadata_errors({**fact, 'domain':hint, 'domain_is_hint':True})
    if not runner.subject_in_fact(fact): errors.append({'code':'E_SUBJECT','detail':'no verbatim name/alias'})
    if len(fact.get('aliases', [])) > 10: errors.append({'code':'E_ALIASES','detail':'more than 10 aliases'})
    if errors: return copy.deepcopy(original), errors
    assert fact['fact'] == original['fact']
    return fact, []


def run(plan, home, paid):
    import pipeline as p
    from area_catalog import confirmed
    confirmed(plan, home)
    if not plan.get('subject_shorten'): raise Stop('subject-shorten not enabled by plan')
    root = home/'state/subject-shorten'
    frozen_path = root/'input.json'
    binding = digest(plan)
    if frozen_path.exists():
        frozen = load(frozen_path)
        if frozen['binding'] != binding: raise Stop('subject-shorten plan drift')
    else:
        if (home/'state/database.dump').exists(): raise Stop('subject-shorten must precede absorption')
        records = {}
        for record in plan['records']:
            g = load(home/'state/generated'/(record['record_id']+'.json'))
            if g.get('source_sha') != record['sha256']: raise Stop('subject-shorten source drift')
            records[record['record_id']] = g
        frozen = {'binding':binding, 'records':records}
        save(frozen_path, frozen)
    # Reserve all unchanged names within each hint. Global mapping guarantees identical
    # long spelling across records; a short spelling may independently exist in other areas.
    owners = defaultdict(dict)
    occurrences = defaultdict(set)
    for rid, g in frozen['records'].items():
        for fact in g.get('facts', []):
            for obj in objects(fact):
                occurrences[obj['name']].add(g.get('partition_key'))
                if not needs_shortening(obj): owners[g.get('partition_key')][obj['name']] = obj['name']
    mapping = {}; failures = []; outputs = {}
    for rid, g in frozen['records'].items():
        distinct = {}
        for fact in g.get('facts', []):
            for obj in objects(fact):
                if needs_shortening(obj) and obj['name'] not in mapping:
                    distinct.setdefault(obj['name'], obj)
        if distinct:
            packet = {'area_hint':g.get('partition_key'), 'objects':list(distinct.values()),
                      'kept_names':sorted(owners[g.get('partition_key')]), 'known_mappings':mapping}
            receipt_path = root/(rid+'.json')
            cache_path = home/'state/subject-shorten-cache'/(rid+'-'+digest(packet)+'.json')
            if not receipt_path.exists() and cache_path.exists():
                save(receipt_path, load(cache_path))
            if receipt_path.exists():
                receipt = load(receipt_path)
                if receipt['input_sha'] != digest(packet): raise Stop('subject-shorten response drift')
            else:
                paid.scope = 'subject-shorten:'+rid
                try:
                    answer, usage = p.packet_model(paid, 'subject-shorten-'+rid, packet,
                        SHORT_NAME_GUIDANCE + '각 긴 이름을 짧은 명사구 하나로 매핑. 종류/설명/상위 의미 보존. '
                        '서로 다른 개념을 같은 이름으로 합치지 않는다. description은 입력 그대로 반환. '
                        '사실 문장은 입력하지 않으며 재작성 금지. JSON만 '
                        '{"items":[{"original":"긴 이름","name":"짧은 이름","description":"원래 설명"}]}',
                        charge_step='generation')
                    receipt = {'input_sha':digest(packet),'answer':answer,'usage':usage}
                except ValueError as exc:
                    receipt = {'input_sha':digest(packet),'answer':{},'error':str(exc)}
                save(receipt_path, receipt)
            items = receipt['answer'].get('items', [])
            if not isinstance(items, list): items = []
            for old, obj in distinct.items():
                matches = [x for x in items if isinstance(x, dict) and x.get('original') == old]
                errors = []
                item = matches[0] if len(matches) == 1 else {}
                name = item.get('name')
                if not isinstance(name,str): name = None
                errors.extend(short_name_errors(name))
                if runner.subject_error(name): errors.append({'code':'E_SUBJECT_OBJECT','detail':runner.subject_error(name)})
                if item.get('description') != obj['description']:
                    errors.append({'code':'E_SHORTEN_DESCRIPTION','detail':'description must be retained'})
                if len(matches) != 1: errors.append({'code':'E_SHORTEN_MAPPING','detail':'one mapping required'})
                for hint in occurrences[old]:
                    if name in owners[hint] and owners[hint][name] != old:
                        errors.append({'code':'E_SHORTEN_COLLISION','detail':'another concept owns name; assigned-area equality is checked after routing'})
                # Reject collisions inside the same response as well, never first-wins merging.
                if name and sum(isinstance(x,dict) and x.get('name') == name for x in items) > 1:
                    errors.append({'code':'E_SHORTEN_COLLISION','detail':'duplicate short name in response'})
                mapping[old] = {'name':name if not errors else old, 'status':'failed' if errors else 'shortened', 'errors':errors}
                if not errors:
                    for hint in occurrences[old]: owners[hint][name] = old
                else: failures.append({'record_id':rid,'original':old,'errors':errors})
        out = copy.deepcopy(g)
        for i, fact in enumerate(g.get('facts', [])):
            out['facts'][i], errors = apply_fact(fact, mapping, g.get('partition_key'))
            if errors: failures.append({'record_id':rid,'fact_index':i,'original':fact.get('subject'),'errors':errors})
        if plan.get('area_fact_isolation'):
            out, retry = retry_once(home, rid, out, paid)
            failures = [f for f in failures if f['record_id'] != rid]
            failures.extend({'record_id':rid, **f} for f in retry['failures'])
        out['subject_shorten_version'] = 1
        out['subject_shorten_input_sha'] = digest(g)
        path = home/'state/generated'/(rid+'.json')
        current = load(path)
        if current not in (g, out): raise Stop('subject-shorten generated drift')
        save(path, out)
        outputs[rid] = digest(out)
    report = {'binding':binding, 'completed':True, 'mapping':mapping, 'failures':failures,
              'generated':outputs, 'cash_usd':0, 'fact_text_changed':False,
              'retry_once':bool(plan.get('area_fact_isolation')),
              'reused_response_usage':[load(path).get('usage',{}) for path in (home/'state/subject-shorten-cache').glob('*.json')],
              'status':'completed_with_failures' if failures else 'completed'}
    save(home/'state/subject-shorten-report.json', report)
    return report


def retry_once(home, rid, generated, paid):
    """One extra subscription attempt per affected record, including lint errors.
    Separate immutable receipt prevents repeats after failure or process restart.
    """
    import pipeline as p
    distinct = {}
    for fact in generated.get('facts', []):
        for obj in objects(fact):
            errors = short_name_errors(obj['name'])
            if errors: distinct.setdefault(obj['name'], {**obj, 'lint_errors':errors})
    if not distinct: return generated, {'failures':[]}
    packet = {'area_hint':generated.get('partition_key'), 'objects':list(distinct.values()),
              'kept_names':sorted({o['name'] for f in generated['facts'] for o in objects(f)
                                   if o['name'] not in distinct})}
    path = home/'state/subject-shorten-retry'/(rid+'.json')
    if path.exists():
        receipt = load(path)
        if receipt['input_sha'] != digest(packet): raise Stop('shorten retry input drift')
    else:
        paid.scope = 'subject-shorten-retry:'+rid
        try:
            answer, usage = p.packet_model(paid, 'subject-shorten-retry-'+rid, packet,
                SHORT_NAME_GUIDANCE + '이전 축약이 lint_errors로 실패했다. 이번 한 번만 재시도한다. '
                '입력 이름에 근거한 토큰을 남겨 짧게 하고 가상 예시 이름 복사 금지. '
                '종류/상위/description 보존, 서로 다른 개념을 합치지 말고 kept_names와 충돌 금지. '
                'JSON {"items":[{"original":"긴 이름","name":"짧은 이름","description":"원래 설명"}]}',
                charge_step='generation')
            receipt = {'input_sha':digest(packet), 'answer':answer, 'usage':usage}
        except ValueError as exc:
            receipt = {'input_sha':digest(packet), 'answer':{}, 'error':str(exc)}
        save(path, receipt)
    items = receipt['answer'].get('items', [])
    if not isinstance(items, list): items = []
    mapping = {}
    for old, obj in distinct.items():
        matches = [x for x in items if isinstance(x,dict) and x.get('original') == old]
        item = matches[0] if len(matches) == 1 else {}
        name = item.get('name')
        valid = (not short_name_errors(name) and not runner.subject_error(name)
                 and item.get('description') == obj['description']
                 and name not in packet['kept_names']
                 and sum(isinstance(x,dict) and x.get('name') == name for x in items) == 1)
        mapping[old] = {'name':name if valid else old, 'status':'shortened' if valid else 'failed'}
    out = copy.deepcopy(generated); failures = []
    for i, fact in enumerate(generated['facts']):
        out['facts'][i], errors = apply_fact(fact, mapping, generated.get('partition_key'))
        if errors: failures.append({'fact_index':i, 'original':fact.get('subject'), 'errors':errors})
    return out, {'failures':failures}


def assert_completed(plan, home):
    if not plan.get('subject_shorten'): return
    path = home/'state/subject-shorten-report.json'
    if not path.exists(): raise Stop('subject-shorten must complete before embed-precompute/absorb')
    report = load(path)
    frozen = home/'state/subject-ground/input.json'
    if plan.get('subject_ground') and frozen.exists():
        ground_input = load(frozen)
        if ground_input.get('binding') != digest(plan): raise Stop('grounding input plan drift')
        actual = {rid:digest(g) for rid,g in ground_input['records'].items()}
    else:
        actual = {r['record_id']:digest(load(home/'state/generated'/(r['record_id']+'.json'))) for r in plan['records']}
    if report.get('binding') != digest(plan) or not report.get('completed') or report.get('generated') != actual:
        raise Stop('subject-shorten completion drift')
