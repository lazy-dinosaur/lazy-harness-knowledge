"""Trusted, unit-scoped technical display homonyms; not identity or ownership grants."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import copy
import source_role_contract as c
from writer_worker import digest

VERSION='technical-display-homonym/1'
_ACTIVE=ContextVar('technical_display_homonym',default=None)

@dataclass(frozen=True)
class TechnicalNamePolicy:
    host_id:str
    version:str=VERSION

@contextmanager
def technical_names(policy,receipt,unit,source,catalog):
    from domain_source_review import consume,TECHNICAL_POLICY
    from knowledge_forms import source_owner
    if type(policy) is not TechnicalNamePolicy or policy.version!=VERSION or policy.host_id!=unit['host_id']:
        raise ValueError('explicit typed HOST technical-name policy required')
    area,_=consume(receipt,unit,source,catalog)
    if receipt.get('host_semantic_policy')!=TECHNICAL_POLICY or receipt['review']['area_kind'] not in ('shared-technical','component'):
        raise ValueError('technical homonym requires current typed technical area proof')
    if unit.get('knowledge_form') or 'source_owner' not in unit:
        raise ValueError('independent SOURCE entity ownership required; no definition escape')
    owner=source_owner(unit['source_owner'],source)
    if owner['expression']!=unit['target_expression']:
        raise ValueError('operand/ownership substitution forbidden')
    state={'host':policy.host_id,'domain':area['name'],'unit':digest(unit),'source':digest(source),
           'owner':owner['expression'],'subject':None,'ddd':False}
    token=_ACTIVE.set(state)
    try:yield
    finally:_ACTIVE.reset(token)

def admit_name(domain,name,unit,source,ddd_pass):
    state=_ACTIVE.get()
    if (state is None or ddd_pass is not True or state['host']!=unit.get('host_id') or
        state['domain']!=domain.get('name') or state['unit']!=digest(unit) or state['source']!=digest(source) or
        state['owner']!=unit.get('target_expression')):
        return False
    state.update(subject=name,ddd=True)
    return True

def allowed_fact(fact):
    state=_ACTIVE.get()
    if state is None or not state['ddd']:return False
    ref=fact.get('identity_ref',{})
    return (fact.get('assigned_area',fact.get('domain'))==state['domain'] and fact.get('subject')==state['subject']
            and ref.get('host_id')==state['host'] and ref.get('domain')==state['domain'] and bool(ref.get('subject_id')))
