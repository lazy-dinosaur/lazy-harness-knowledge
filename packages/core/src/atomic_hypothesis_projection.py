"""HOST-only atomic hypothesis DATA normalization; never SOURCE truth or admission.

The existing non-child correction partition is deliberately not reused.
"""
import copy
from dataclasses import dataclass
import source_anchor_proof as a
import source_role_contract as c
from writer_worker import digest
from source_context_policy import documentary_field_lines

KEY = 'atomic_hypothesis_projection'

@dataclass(frozen=True)
class AtomicHypothesisPolicy:
    lineage: a.RetainedChildPolicy
    raw_binding: str
    required_children: int
    # Exact HOST-reviewed documentary prefix, not a regex or a generated answer.
    documentary_prefix: str
    documentary_ranges: tuple


def project(unit, source, policy):
    if type(policy) is not AtomicHypothesisPolicy:
        raise ValueError('trusted atomic hypothesis policy required; HOLD')
    if KEY in unit or policy.required_children != 1 or digest(unit) != policy.raw_binding:
        raise ValueError('single frozen child/raw binding required; HOLD')
    raw = a.bind_retained_child(unit, source, policy.lineage)
    if raw != unit:
        raise ValueError('already bound frozen raw child required; HOLD')
    child = raw['atomic_child']
    prefix = policy.documentary_prefix
    conditions = child.get('conditions')
    if (not isinstance(conditions, list) or len(conditions) != 1
        or not isinstance(prefix, str) or not prefix or not child['claim'].startswith(prefix)
        or not child['claim'][len(prefix):].strip()):
        raise ValueError('one wholly documentary antecedent required; mixed/unknown HOLD')
    ranges = [list(r) for r in policy.documentary_ranges]
    fields = documentary_field_lines(source)
    for anchor in c.anchors(source, ranges):
        if not set(range(anchor['start'], anchor['end'] + 1)) <= fields:
            raise ValueError('documentary-only standard field required; mixed/unknown HOLD')
    if not ranges:
        raise ValueError('documentary ranges required; HOLD')
    parent = policy.lineage.original_parent()
    # The selected parent proposition remains exact SOURCE content; the effective
    # child is a suffix of the raw child, never all consequences of a wider parent.
    for anchor in c.anchors(source, parent['source_ranges']):
        if parent['content'] not in anchor['quote']:
            raise ValueError('selected parent SOURCE claim drift; HOLD')
    value = {'raw_unit': copy.deepcopy(raw), 'source_sha256': a.source_sha(source),
             'parent_identity': a.identity(parent, source),
             'required_children': 1, 'documentary_prefix': prefix,
             'condition_roles': [{'condition': conditions[0], 'role': 'documentary-trigger',
                                  'source_ranges': ranges}],
             'effective': {'claim': child['claim'][len(prefix):], 'conditions': [],
                           'selected_claim_ranges': copy.deepcopy(parent['source_ranges']),
                           'documentary_context_ranges': ranges},
             'not_source_truth_or_authority': True}
    result = copy.deepcopy(raw)
    result[KEY] = value
    return result


def identity(unit, source):
    """Rebuild exact typed diff before a semantic key; transport changes buy nothing."""
    value = unit[KEY]
    raw = value['raw_unit']
    parent = raw['atomic_child']['parent_hypothesis']
    lineage = a.RetainedChildPolicy(raw['host_id'], lambda: copy.deepcopy(parent),
                                    digest(parent), a.source_sha(source))
    roles = value['condition_roles']
    if len(roles) != 1 or roles[0]['role'] != 'documentary-trigger':
        raise ValueError('mixed/unknown condition roles HOLD')
    policy = AtomicHypothesisPolicy(lineage, digest(raw), value['required_children'],
                                   value['documentary_prefix'],
                                   tuple(tuple(r) for r in roles[0]['source_ranges']))
    if project(raw, source, policy) != unit:
        raise ValueError('raw lineage/effective meaning/condition role drift; HOLD')
    return {'raw': c.semantic_unit(raw), 'parent': c.semantic_unit(parent),
            'source_sha256': value['source_sha256'], 'effective': copy.deepcopy(value['effective']),
            'condition_roles': copy.deepcopy(roles)}


def effective(unit, source):
    identity(unit, source)
    return copy.deepcopy(unit[KEY]['effective'])
