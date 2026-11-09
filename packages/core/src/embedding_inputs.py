"""Inputs shared by unit warmup and offline pilot precompute (before E5 prefixes)."""

def collect(facts, partition=''):
    import fact_form
    import runner
    import subject_dict
    import store_pg
    queries, passages = [], []
    for fact in facts:
        if not isinstance(fact, dict) or not isinstance(fact.get('fact'), str):
            continue
        text = fact['fact']
        queries.extend([text, text[:300]])  # routing, worktime/digest, initial domain description
        passages.append(store_pg._embedding_input(None, None, [], text, 'plain'))
        if fact.get('assigned_area'):
            parent = fact.get('parent_subject') or {}
            passages.append(store_pg._embedding_input(None, fact['assigned_area'], [], text, 'ctx-v2',
                                                       fact.get('subject'), parent.get('name')))
        names = [fact.get('subject')]
        parent = fact.get('parent_subject') or {}
        names.append(parent.get('name'))
        for leaf in fact_form.leaves(fact_form.parse(text)):
            head = runner.SUBJECT_HEAD.match(leaf['text'] + ' ')
            if head:
                names.append(head.group(1).strip())
        names.extend(h.get('as_written') for h in fact.get('leaf_provisional', []))
        queries.extend(subject_dict.core(n) for n in names if isinstance(n, str) and n.strip())
        queries.extend(subject_dict.identity_text(n,d) for n,d in ((fact.get('subject'),fact.get('subject_description')), (parent.get('name'),parent.get('description'))) if n)
        queries.extend(d for d in (fact.get('subject_description'), parent.get('description'),
                                   fact.get('domain_description'), fact.get('domain'), partition) if isinstance(d, str) and d)
    return {'query':list(dict.fromkeys(queries)), 'passage':list(dict.fromkeys(passages))}


def warm(facts, partition=''):
    import embed
    values = collect(facts, partition)
    embed.encode_queries(values['query'])
    embed.encode_passages(values['passage'])
    return values
