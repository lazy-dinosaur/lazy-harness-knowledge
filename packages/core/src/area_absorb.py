"""Area rebuild: route all facts first, then isolate record lint failures.
Receipts bind original fact indices; held facts remain visible outside the DB.
"""
from safety import digest, load, save, Stop


def assigned_area(plan, home, dsn, host, record, generated, adapters):
    import store_pg as pg
    import domain_router as dr
    import subject_dict as sd
    import embed
    path = home/'state/area-assignments'/(record['record_id']+'.json')
    binding = digest([plan, generated])
    if path.exists():
        receipt = load(path)
        if receipt['binding'] != binding: raise Stop('area assignment input drift')
        domain, description = receipt['domain'], receipt['description']
    else:
        texts = [f.get('fact','') for f in generated['facts']]
        vector = adapters['embed_fn']([dr.document_text(texts)])[0]
        with pg.connect(dsn) as conn, conn.cursor() as cur:
            domains = dr.load(cur, host)
            candidates = {d:domains[d] for d in dr.nearest(cur, host, vector)}
        domain, how = dr.route_unit(candidates, generated['partition_key'], texts,
            adapters['choose'], adapters.get('confirm'), adapters['name_area'])
        description = candidates[domain]['description']
        save(path, {'binding':binding, 'domain':domain, 'description':description, 'how':how})
    # Reinstall safely on resume if a receipt was saved before the DB checkpoint.
    with pg.connect(dsn) as conn, conn.cursor() as cur:
        dr.ensure(cur, host, domain, description)
        cur.execute('select 1 from knowledge.domain_embedding where host_id=%s and domain=%s and model_id=%s',
                    (host, domain, embed.MODEL_ID))
        if not cur.fetchone():
            vector = adapters['embed_fn']([sd.identity_text(domain, description)])[0]
            cur.execute('''insert into knowledge.domain_embedding(host_id,domain,model_id,embedding)
                values(%s,%s,%s,%s::extensions.vector) on conflict do nothing''',
                (host,domain,embed.MODEL_ID,sd._vec(vector)))
    return domain


def project_holds(record, receipt):
    from pipeline import disposition
    held = receipt.get('held_facts', [])
    indices = receipt.get('recorded_indices', [])
    record['held_facts'] = held
    record['held_fact_count'] = len(held)
    record['assigned_area'] = receipt.get('assigned_area')
    for fact in record['facts']:
        index = fact['fact_index']
        if 0 <= index < len(indices): fact['source_fact_index'] = indices[index]
    status = disposition(record['expected_facts'] - len(held),
                         [f['decision'] for f in record['facts']], record['vector_or_structure_missing'])
    if held:
        record['status'] = 'absorbed_except_held' if status == 'absorbed' else 'held'
        record['disposition'] = (f'document absorbed except {len(held)} held facts' if status == 'absorbed'
                                 else f'{len(held)} lint-held facts; remaining disposition: {status}')
    return record
