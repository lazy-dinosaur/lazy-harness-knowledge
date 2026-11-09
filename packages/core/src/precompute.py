"""DB-free embedding preparation, paid response reuse and strict completion receipt."""
import hashlib
from pathlib import Path
from safety import Stop, digest, load, save


def inventory(plan, home):
    from embedding_inputs import collect
    if plan.get('write_path'):
        from place_write import assert_completed
        assert_completed(plan,home)
    else:
        from subject_shorten import assert_completed as assert_shortened
        assert_shortened(plan,home)
        from subject_ground import assert_completed as assert_grounded
        assert_grounded(plan,home)
    values = {'query':[], 'passage':[]}
    generated = {}
    scopes = set()
    for record in plan['records']:
        path = home / 'state/generated' / (record['record_id'] + '.json')
        if not path.exists():
            raise Stop('generation must complete before embed-precompute')
        g = load(path)
        if g.get('source_sha') != record['sha256']:
            raise Stop('precompute generated source drift')
        if not plan.get('write_path') and plan.get('fresh_database') and (g.get('subject_fix_version') != 2 or g.get('subject_refix_version') != 2):
            raise Stop('subject-fix/refix must complete before embed-precompute')
        generated[record['record_id']] = digest(g)
        scopes.add(record['record_id'])
        scopes.add('subject-refix-absorb:' + record['record_id'])
        partition = g.get('partition_key', '')
        for checkpoint in ('domain-batch', 'final', 'domain-batch:subject-refix', 'final:subject-refix'):
            scopes.add(checkpoint + ':domain:' + partition)
        for role, texts in collect(g.get('facts', []), partition).items():
            values[role].extend(texts)
    if plan.get('area_catalog') and not plan.get('write_path'):
        from area_catalog import confirmed
        from domain_router import document_text
        from subject_dict import identity_text
        catalog = confirmed(plan,home)
        values['query'].extend(identity_text(row['name'],row['description']) for row in catalog)
        # Routing happens at absorb. Prepare each confirmed area with the SAME formatter
        # as runtime, never use the generation partition hint as the area.
        for r in plan['records']:
            g = load(home/'state/generated'/(r['record_id']+'.json'))
            for area in catalog:
                fs = [{**f, 'assigned_area':area['name']} for f in g.get('facts',[])]
                values['passage'].extend(collect(fs)['passage'])
        for r in plan['records']:
            g=load(home/'state/generated'/(r['record_id']+'.json'))
            values['query'].append(document_text([f['fact'] for f in g.get('facts',[])]))
    # Previously Luna-written descriptions: paid output is authoritative even if DB transaction rolled back.
    ledger = home / 'state/paid.jsonl'
    rows = [__import__('json').loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
    for row in rows:
        if row['state'] != 'done' or row['step'] != 'maintenance':
            continue
        value = load(home / 'state' / row['result'])
        if digest(value) != row['sha']:
            raise Stop('paid result drift')
        if 'text' in value:
            from pipeline import parse_model
            from model_output import ModelOutputInvalid
            try:
                description = parse_model(value['text']).get('description')
            except ModelOutputInvalid:
                continue
            if isinstance(description, str) and description:
                values['query'].extend([description, description[:300]])
    for role in values:
        # Providers reject empty inputs (OpenRouter 400 too_small): records without facts give an empty document text.
        values[role] = list(dict.fromkeys(t for t in values[role] if isinstance(t, str) and t.strip()))
    return values, generated, sorted(scopes)
def seed_paid(paid, values, scopes, model=None):
    """Reuse exact request bodies/hashes only, in their OWN model space. Raw pplx roles are equivalent."""
    import embed
    from embedding_models import E5, format_inputs
    model = model or embed.REMOTE_MODEL
    model_id = model + '@openrouter'
    rows = {r['id']:r for r in paid.ledger.rows}
    done = {}
    for row in rows.values():
        if row['step'] == 'embedding' and row['state'] == 'done':
            done[row['id']] = row
            if row.get('imported_from',{}).get('id'):
                done[row['imported_from']['id']] = row
    url = paid.cfg['jev_base_url'].rstrip('/')+'/v1/embeddings'
    seen = set()
    reused = 0

    def store(row, body):
        nonlocal reused
        if row['id'] in seen or body.get('model') != model:
            return
        inputs = body.get('input',[])
        if not inputs or not all(isinstance(t,str) for t in inputs):
            raise Stop('paid embedding input shape')
        if model == E5:
            if any(not t.startswith(('query: ','passage: ')) for t in inputs):
                raise Stop('paid e5 prefix missing')
            roles = {t.split(': ',1)[0] for t in inputs}
            if len(roles) != 1:
                raise Stop('mixed embedding roles')
            texts = [t.split(': ',1)[1] for t in inputs]
        else:
            # Same exact raw request for both roles; materialize both separately keyed cache entries.
            roles, texts = ('query','passage'), inputs
        result = load(paid.ledger.path.parent/row['result'])
        if digest(result) != row['sha']:
            raise Stop('paid result drift')
        # Providers may echo the bare name without the vendor prefix (see embedding_transport.send).
        if result.get('model',model) not in (model, model.split('/',1)[-1]):
            raise Stop('paid result embedding model mismatch')
        data = sorted(result['data'],key=lambda x:x['index'])
        if [d['index'] for d in data] != list(range(len(inputs))) or any(len(d['embedding']) != 1024 for d in data):
            raise Stop('paid embedding shape mismatch')
        for role in roles:
            embed.CACHE.put_many(model_id,role,texts,[d['embedding'] for d in data])
        seen.add(row['id']); reused += len(inputs)

    for row in rows.values():
        request = row.get('embedding_request')
        if row['state']=='done' and row['step']=='embedding' and request and request.get('url')==url:
            store(row,request['body'])
    if model != E5:
        return reused  # legacy pre-journal requests were exclusively e5, never silently relabel them
    for role,texts in values.items():
        chunks = [[t] for t in texts if embed.CACHE.get(model_id,role,t) is None]
        chunks += [texts[i:i+n] for n in (16,64) for i in range(0,len(texts),n)]
        for chunk in chunks:
            body = {'model':model,'input':format_inputs(model,chunk,role)}
            matched = False
            for scope in scopes:
                keys = [[scope,url,body]]+[[scope,url,body,'hedged-v1',a,b] for a in range(4) for b in range(2)]
                for key in keys:
                    row = done.get(digest(['embedding',key]))
                    if row:
                        store(row,body); matched=True; break
                if matched:
                    break
    return reused


def run(plan, home, paid):
    import embed
    values, generated, scopes = inventory(plan, home)
    paid.scope = 'embed-precompute-v1'
    reused = seed_paid(paid, values, scopes)
    before = embed.CACHE.stats()
    embed.encode_queries(values['query'])
    embed.encode_passages(values['passage'])
    result = {'completed':True, 'binding':digest(plan), 'model':embed.MODEL_ID,
              'generated':generated, 'keys':{role:len(texts) for role,texts in values.items()},
              'paid_vectors_reused':reused,
              'cache':{k:embed.CACHE.stats()[k]-before[k] for k in before}}
    save(home / 'state/embed-precompute.json', result)
    return result

def assert_completed(plan, home):
    import embed
    path = home / 'state/embed-precompute.json'
    if not path.exists():
        raise Stop('embed-precompute must complete before absorb')
    receipt = load(path)
    values, generated, _ = inventory(plan, home)
    if receipt.get('completed') is not True or receipt.get('binding') != digest(plan) or receipt.get('model') != embed.MODEL_ID or receipt.get('generated') != generated:
        raise Stop('embed-precompute receipt drift')
    # Maintenance can add descriptions after the receipt. Frozen generated inputs must still be present.
    from embedding_inputs import collect
    for record in plan['records']:
        g = load(home / 'state/generated' / (record['record_id'] + '.json'))
        for role, texts in collect(g.get('facts', []), g.get('partition_key', '')).items():
            if any(embed.CACHE.get(embed.MODEL_ID, role, text) is None for text in texts):
                raise Stop('precomputed vector missing; rerun embed-precompute')
