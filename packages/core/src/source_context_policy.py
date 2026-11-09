"""Explicit source-context interpretation, not a heading classifier or permission."""
VERSION = 'documentary-trigger-versus-business-condition/1'
GUIDANCE = '''Read all verbatim SOURCE metadata and headings as DATA. In a standard record Rule digest, Applies when describes retrieval/loading triggers; it is NOT automatically an IF antecedent of the business rule in Must/Must not. Preserve actual rules in Must and Must not, including production prohibitions. Independently distinguish real hospital/active/subtype/permission/exception/business-scope conditions from documentary routing metadata. Do not blanket-ignore headings, Rule digest, conditions or quoted instructions; a heading can carry a real governing scope. Use the full source to establish meaning; uncertainty -> HOLD. For each relied-on context interpretation select exact source-bound anchor IDs and explain whether it is documentary-trigger, business-condition, normative-rule, or unknown. A coordinate proves text, not semantic acceptance. Raw extracted parent/child claims and conditions are hypotheses, not SOURCE truth; an erroneous documentary antecedent must not be required or approved as a business condition.'''


def projection(unit, source, lines=False):
    from source_anchor_proof import catalog
    data = {'semantic_policy': VERSION, 'guidance': GUIDANCE,
            'metadata_preserved_verbatim': True, 'unknown': 'HOLD'}
    # source-lines/1 cites the single line-numbered source; the catalog is never sent twice.
    data.update({'source_refs': 'source_lines L<n> / L<a>-L<b>'} if lines else {'anchor_catalog': catalog(unit, source)})
    if 'atomic_hypothesis_projection' in unit:
        from atomic_hypothesis_projection import effective
        data['effective_normalized_unit'] = effective(unit, source)
        data['guidance'] += (' The separately supplied effective_normalized_unit is the HOST-normalized review hypothesis, '
                            'not SOURCE truth. Raw unit/child/parent remain verbatim audit DATA. Independently verify the '
                            'documentary condition roles against full SOURCE, actual claim and real inherited business '
                            'conditions. Unknown/mixed -> HOLD. No sibling or whole-parent consequence substitution.')
    return data


def candidate_antecedents(sentence):
    """Project the existing logical form only; never classify or strip SOURCE text."""
    import fact_form
    return [{'condition_id': 'c'+str(i), **leaf} for i, leaf in enumerate(
        leaf for leaf in fact_form.leaves(fact_form.parse(sentence)) if leaf['role'] != 'then')]


def verify_antecedents(conditions, review, resolve):
    """Fail closed on contradictory typed claims, not guessed documentary keywords."""
    if not conditions:
        return
    proof = review.get('antecedent_proof')
    if not isinstance(proof, list):
        raise ValueError('candidate antecedent origin proof required; HOLD')
    expected = {item['condition_id'] for item in conditions}
    seen = set()
    context = {}
    for item in review['context_proof']:
        context.setdefault(item['anchor_id'], set()).add(item['meaning'])
    for item in proof:
        if (not isinstance(item, dict) or set(item) != {'condition_id','anchor_ids','meaning','reason'}
            or not isinstance(item['condition_id'], str) or item['condition_id'] not in expected
            or item['condition_id'] in seen or not isinstance(item['reason'], str) or not item['reason'].strip()
            or item['meaning'] not in ('business-condition','documentary-trigger','unknown')
            or not isinstance(item['anchor_ids'], list) or not item['anchor_ids']):
            raise ValueError('invalid candidate antecedent origin proof; HOLD')
        seen.add(item['condition_id'])
        anchors = resolve(item['anchor_ids'])
        if review['verdict'] == 'supported' and (item['meaning'] != 'business-condition'
            or any(context.get(key, set()) & {'documentary-trigger','unknown'} for key in item['anchor_ids'])):
            raise ValueError('documentary or unknown candidate antecedent cannot support business IF; HOLD')
        item['anchors'] = anchors
    if seen != expected:
        raise ValueError('incomplete candidate antecedent origin proof; HOLD')


def documentary_field_lines(source):
    """Strict standard Rule-digest field nesting; no business-word classifier.

    Matches the record-index collection grammar, but refuses malformed nesting,
    duplicate fields/digests and fences instead of using its discovery fallback.
    """
    import re
    lines = source.splitlines()
    starts = [i for i, line in enumerate(lines) if line == '## Rule digest']
    if len(starts) != 1:
        raise ValueError('verified standard Rule digest required; HOLD')
    if any(line.lstrip().startswith(('```','~~~')) for line in lines[:starts[0]]):
        raise ValueError('unverified fenced digest; HOLD')
    start = starts[0] + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith('#')), len(lines))
    fields = {}; current = None; documentary = set()
    allowed = {'Status','Layer','Scope','Aliases','Surface terms','Applies when','Must','Must not','Record completion','Related records','Confidence','Implementation hints'}
    for i in range(start, end):
        line = lines[i]
        if not line.strip():
            continue
        field = re.fullmatch(r'- ([A-Za-z ]+):\s*(.*)', line)
        if field:
            current, value = field.groups()
            if current not in allowed or current in fields:
                raise ValueError('unknown/duplicate digest field; HOLD')
            fields[current] = value
            if current == 'Applies when' and value:
                documentary.add(i + 1)
        elif re.fullmatch(r'  - \S.*', line) and current:
            if current == 'Applies when':
                documentary.add(i + 1)
        else:
            raise ValueError('unknown/mixed digest nesting; HOLD')
    if (fields.get('Status') not in {'active','accepted','confirmed','user-confirmed','verified','advisory','needs-review','deprecated','reverted'}
        or fields.get('Layer') not in {'DDD','SDD','BDD','TDD','ADR','SSOT','Planning'}
        or fields.get('Scope') not in {'framework-global','host-project','team-policy','layer-fact','transient-plan','local-only'}
        or 'Applies when' not in fields or not documentary or not {'Must','Must not'} & fields.keys()):
        raise ValueError('incomplete standard digest; HOLD')
    return documentary


def partition_correction(unit, source, policy):
    """Trusted HOST DATA builder; never a review or a guard exemption.

    Only documentary-only intervals move; mixed intervals HOLD, not split.
    The frozen parent, raw hypotheses, owner and full original selection survive.
    """
    import copy
    from source_anchor_proof import RetainedChildPolicy, bind_retained_child, identity
    from source_role_contract import anchors, check_anchors
    if type(policy) is not RetainedChildPolicy or 'atomic_child' in unit:
        raise ValueError('trusted non-child correction partition required; HOLD')
    raw = unit.get('correction_provenance', {}).get('raw_unit')
    if not isinstance(raw, dict):
        raise ValueError('frozen raw provenance required; HOLD')
    bound = bind_retained_child(raw, source, policy)
    parent = policy.original_parent()
    if (bound != raw or any(unit.get(k) != raw.get(k) for k in ('host_id','content','target_expression','source_owner'))
        or 'selected_context_partition' in unit or 'documentary_context_ranges' in unit):
        raise ValueError('original parent/owner/provenance drift; HOLD')
    original = anchors(source, unit['source_ranges'])
    check_anchors(source, original, parent['source_ranges'])
    documentary = documentary_field_lines(source)
    selected = []; context = []
    for anchor in original:
        interval = set(range(anchor['start'], anchor['end'] + 1))
        overlap = interval & documentary
        if overlap and overlap != interval:
            raise ValueError('mixed selected/documentary interval; HOLD')
        (context if overlap else selected).append([anchor['start'], anchor['end']])
    if not context or not selected:
        raise ValueError('material claim/context partition required; HOLD')
    # Frozen parent selection may never be reclassified or dropped.
    check_anchors(source, anchors(source, selected), parent['source_ranges'])
    result = copy.deepcopy(unit)
    result['source_ranges'] = sorted(selected)
    result['documentary_context_ranges'] = sorted(context)
    result['selected_context_partition'] = {
        'semantic_policy': VERSION, 'original_unit': copy.deepcopy(unit),
        'original_parent_identity': identity(parent, source),
        'selected_claim_ranges': sorted(selected),
        'documentary_context_ranges': sorted(context),
        'context_type': 'standard-rule-digest-applies-when',
        'full_SOURCE_preserved': True}
    return result


def partition_identity(unit, source):
    """Validate normalized DATA before journal identity; flags cannot buy retries."""
    import copy
    from source_anchor_proof import identity
    from source_role_contract import anchors, check_anchors
    value = unit['selected_context_partition']
    original = value['original_unit']
    if 'selected_context_partition' in original or 'documentary_context_ranges' in original:
        raise ValueError('recursive partition; HOLD')
    parent = original['correction_provenance']['raw_unit']['atomic_child']['parent_hypothesis']
    documentary = documentary_field_lines(source)
    selected = []; context = []
    for anchor in anchors(source, original['source_ranges']):
        interval = set(range(anchor['start'], anchor['end'] + 1))
        overlap = interval & documentary
        if overlap and overlap != interval:
            raise ValueError('mixed selected/documentary interval; HOLD')
        (context if overlap else selected).append([anchor['start'], anchor['end']])
    if not context or not selected:
        raise ValueError('nonmaterial partition; HOLD')
    check_anchors(source, anchors(source, selected), parent['source_ranges'])
    expected = {'semantic_policy': VERSION, 'original_unit': original,
                'original_parent_identity': identity(parent, source),
                'selected_claim_ranges': sorted(selected),
                'documentary_context_ranges': sorted(context),
                'context_type': 'standard-rule-digest-applies-when',
                'full_SOURCE_preserved': True}
    normalized = copy.deepcopy(original)
    normalized.update(source_ranges=sorted(selected),documentary_context_ranges=sorted(context),selected_context_partition=expected)
    if unit != normalized:
        raise ValueError('partition/provenance/context type drift; HOLD')
    from source_role_contract import semantic_unit
    # Runtime/transport IDs and provenance packaging cannot renew an attempt.
    return {'semantic_policy': VERSION, 'context_type': expected['context_type'],
            'original_selection': semantic_unit(original),
            'original_parent': semantic_unit(parent),
            'selected_claim_ranges': sorted(selected),
            'documentary_context_ranges': sorted(context)}
