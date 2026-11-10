"""Explicit knowledge forms, not a classifier or an entity-policy override.

A definition shares the existing canonical fragment/identity carrier, but its
subject is a term, never a claimed entity owner. No schema or enum migration.
"""
import copy
import re
from uuid import NAMESPACE_URL, uuid5

VERSION = 'knowledge-form/1'
DEFINITION_CRITERION = (
    '명시 선택된 definition 형식이다. 엔티티 소유자 판정이 아니다. '
    'unit의 term/target_expression이 source 원문에 실제 명시된 정의 대상 용어이며 '
    'unit.content가 그 용어의 뜻을 설명하는가? 과정/역할도 정의 대상 용어일 수 있지만 '
    '정의에 없는 수행자/소유자/효과를 만들면 아니오다. 예/아니오 확률로 답한다.')


def declaration(value):
    if (not isinstance(value, dict) or set(value) != {'kind', 'version', 'term'}
            or value['kind'] != 'definition' or type(value['version']) is not int
            or value['version'] != 1 or not isinstance(value['term'], str)
            or not value['term'].strip() or value['term'] != value['term'].strip()
            or len(value['term']) > 120 or '\n' in value['term']):
        raise ValueError('E_KNOWLEDGE_FORM: explicit definition/v1 term required')
    return copy.deepcopy(value)


def identity(host, domain, value):
    form = declaration(value)
    key = re.sub(r'\s+', ' ', form['term']).casefold()
    return str(uuid5(NAMESPACE_URL, '\x1f'.join((VERSION, host, domain, key))))


def errors(fact):
    if 'knowledge_form' not in fact:
        return []  # legacy entity facts retain their unchanged contract
    try:
        form = declaration(fact['knowledge_form'])
        if form['term'] not in [fact.get('subject'), *fact.get('aliases', [])]:
            raise ValueError('definition term must equal the selected name or declared alias')
        if form['term'] not in fact.get('fact', '') and fact.get('subject', '') not in fact.get('fact', ''):
            raise ValueError('definition sentence must contain its term/name')
        if not fact.get('evidence_refs'):
            raise ValueError('definition requires actual typed source citations')
    except (ValueError, TypeError, KeyError) as exc:
        return [{'code': 'E_KNOWLEDGE_FORM', 'where': 'knowledge_form', 'detail': str(exc)}]
    return []


def source_owner(value, document):
    if (not isinstance(value,dict) or set(value)!={'version','expression','source_ranges'}
            or type(value['version']) is not int or value['version']!=1
            or not isinstance(value['expression'],str) or not value['expression'].strip()
            or not isinstance(value['source_ranges'],list) or not value['source_ranges']
            or not isinstance(document,str) or not document):
        raise ValueError('E_SOURCE_OWNER: explicit sourced owner binding required')
    lines=document.splitlines();quotes=[]
    for bounds in value['source_ranges']:
        if (not isinstance(bounds,list) or len(bounds)!=2 or any(type(n) is not int for n in bounds)
                or not 1<=bounds[0]<=bounds[1]<=len(lines)):
            raise ValueError('E_SOURCE_OWNER: invalid antecedent range')
        quotes.append('\n'.join(lines[bounds[0]-1:bounds[1]]))
    if value['expression'] not in '\n'.join(quotes):
        raise ValueError('E_SOURCE_OWNER: owner expression absent from actual antecedent')
    return copy.deepcopy(value)


def canonical_source(fact):
    owner = fact.get('source_unit', {}).get('source_owner')
    extra = {'source_owner': copy.deepcopy(owner)} if owner else {}
    correction = fact.get('source_unit', {}).get('source_owner_correction')
    if correction: extra['source_owner_correction'] = copy.deepcopy(correction)
    if 'knowledge_form' not in fact:
        return extra
    failure = errors(fact)
    if failure:
        raise ValueError(str(failure))
    return {**extra, 'knowledge_form': declaration(fact['knowledge_form'])}


def read(fragment):
    """Public logical view of a real canonical fragment; legacy facts unchanged."""
    source = fragment.get('source') or {}
    form = source.get('knowledge_form')
    if form is None:
        return {'kind': 'entity-fact', 'fragment_id': str(fragment['id']),
                'revision': fragment['revision'], 'text': fragment['text']}
    form = declaration(form)
    domain = fragment['domain']
    return {**form, 'definition_id': identity(fragment['host_id'], domain, form),
            'domain': domain, 'fragment_id': str(fragment['id']),
            'revision': fragment['revision'], 'definition': fragment['text'],
            'citations': copy.deepcopy(source.get('evidence_refs', []))}
