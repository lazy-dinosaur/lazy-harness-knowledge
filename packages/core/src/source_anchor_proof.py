"""Trusted SOURCE coordinates and audited v1 DATA recovery; never semantic admission."""
import copy
import hashlib
from dataclasses import dataclass
from typing import Callable
import source_role_contract as c
from writer_worker import digest

VERSION='source-anchor-proof/2'

def source_sha(source):
    return hashlib.sha256(source.encode('utf-8')).hexdigest()

def unique_quote(source, quote):
    """Exact complete-line equality only: no fuzzy, substring or first-hit recovery."""
    lines=source.splitlines(); wanted=quote.splitlines()
    if not wanted or '\n'.join(wanted)!=quote:raise ValueError('nonliteral source quote')
    hits=[i for i in range(len(lines)-len(wanted)+1) if lines[i:i+len(wanted)]==wanted]
    if len(hits)!=1:raise ValueError('source quote location absent or ambiguous')
    return c.anchors(source,[[hits[0]+1,hits[0]+len(wanted)]])[0]

def catalog(parent, source, extra_quotes=()):
    sha=source_sha(source); lines=source.splitlines(keepends=True); offsets=[0]
    for line in lines:offsets.append(offsets[-1]+len(line.encode('utf-8')))
    ranges={(i,i) for i in range(1,len(source.splitlines())+1)}
    ranges.update(map(tuple,parent['source_ranges']))
    for quote in extra_quotes:
        a=unique_quote(source,quote);ranges.add((a['start'],a['end']))
    selected=set(map(tuple,parent['source_ranges'])); entries=[]
    for start,end in sorted(ranges):
        a=c.anchors(source,[[start,end]])[0]
        # byte_end excludes only the final line terminator, as does the exact quote.
        byte_start=offsets[start-1];byte_end=offsets[end]-len(lines[end-1].encode())+len(lines[end-1].rstrip('\r\n').encode())
        raw=source.encode()[byte_start:byte_end].decode()
        entries.append({**a,'id':digest([VERSION,sha,start,end,a['quote']]),'byte_start':byte_start,'byte_end':byte_end,
                        'byte_text':raw,'sha256':hashlib.sha256(raw.encode()).hexdigest(),
                        'role':'selected-coordinate' if (start,end) in selected else 'reference-context'})
    return {'version':VERSION,'source_sha256':sha,'parent_binding':digest(parent),'entries':entries}

def checked_catalog(parent,source,value):
    selected=set(map(tuple,parent['source_ranges']))
    expected=catalog(parent,source,[a['quote'] for a in value['entries'] if a['start']!=a['end'] and (a['start'],a['end']) not in selected])
    if value!=expected:raise ValueError('stale or forged SOURCE anchor catalog/current binding')
    return {a['id']:a for a in expected['entries']}

def resolve(parent,source,value,ids):
    table=checked_catalog(parent,source,value)
    if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)) or any(i not in table for i in ids):
        raise ValueError('unknown or duplicate SOURCE anchor ID')
    return [{k:table[i][k] for k in ('start','end','quote')} for i in ids]

def resolve_concept(unit,source,value,proof):
    """Resolve an explicit minimal concept span separately from claim support."""
    fields={'anchor_id','start','end','explicit_concept','cohesive_scope','minimal','reason'}
    if not isinstance(proof,dict) or set(proof)!=fields:
        raise ValueError('explicit minimal concept proof required')
    table=checked_catalog(unit,source,value)
    anchor=table.get(proof['anchor_id'])
    if (anchor is None or anchor['start']!=anchor['end'] or
        any(proof[k] is not True for k in ('explicit_concept','cohesive_scope','minimal')) or
        not isinstance(proof['reason'],str) or not proof['reason'].strip()):
        raise ValueError('unknown/nonminimal concept or cohesive scope HOLD')
    start,end=proof['start'],proof['end'];quote=anchor['quote']
    if type(start) is not int or type(end) is not int or not 0<=start<end<=len(quote):
        raise ValueError('invalid concept span')
    text=quote[start:end]
    if not text.strip() or text!=text.strip() or quote.count(text)!=1 or text==source.strip():
        raise ValueError('ambiguous/blanket concept proof')
    return text,{'version':1,**copy.deepcopy(proof),'anchor':copy.deepcopy(anchor),
                 'text':text,'source_sha256':source_sha(source),'unit_binding':digest(unit)}

def identity(parent,source):
    return {'origin':'HOSTDATA—not model-returned support','source_sha256':source_sha(source),
            'parent_binding':digest(parent),'selected_claim_hypothesis':parent['content'],
            'anchors':c.anchors(source,parent['source_ranges'])}




@dataclass(frozen=True)
class RetainedChildPolicy:
    """HOST-injected frozen-manifest reader and independent pins, never caller JSON."""
    host_id: str
    original_parent: Callable
    parent_binding: str
    source_sha256: str


def bind_retained_child(unit, source, policy):
    """Attach missing lineage DATA only; preserve/reject existing values, never reseal."""
    if type(policy) is not RetainedChildPolicy or not callable(policy.original_parent) or policy.host_id != unit.get('host_id'):
        raise ValueError('explicit trusted retained-child policy required')
    parent = copy.deepcopy(policy.original_parent())
    if (not isinstance(parent, dict) or 'atomic_child' in parent or parent.get('host_id') != policy.host_id
        or digest(parent) != policy.parent_binding or source_sha(source) != policy.source_sha256):
        raise ValueError('frozen original parent/full SOURCE pin mismatch')
    atomic = unit.get('atomic_child')
    if (not isinstance(atomic, dict) or atomic.get('parent_id') != parent.get('unit_id')
        or not isinstance(atomic.get('child_id'), str) or not atomic['child_id'] or atomic.get('parent_ranges') != parent.get('source_ranges')
        or unit.get('source_ranges') != parent.get('source_ranges') or unit.get('content') != parent.get('content')):
        raise ValueError('retained child/original parent identity/ranges/content mismatch')
    expected = {'parent_hypothesis': parent, 'host_parent_identity': identity(parent, source),
                'source_sha256': source_sha(source), 'source_sha': digest(source)}
    if any(k in atomic and atomic[k] != v for k, v in expected.items()):
        raise ValueError('existing retained lineage/hash mismatch; not overwritten')
    c.check_anchors(source, atomic.get('anchors'))
    result = copy.deepcopy(unit)
    for key, value in expected.items():
        result['atomic_child'].setdefault(key, copy.deepcopy(value))
    return result
def derive_v1(parent,source,raw_response,raw_request):
    """Rebuild proof, not the invalid response: preserve raw coordinates and provenance.

    Only structural relocation occurs. Scope/modality/ownership meaning must still be
    independently reviewed against the frozen parent by normal admission.
    """
    env=c.strict_json(raw_request['context']['messages'][0]['content']);data=env['data']
    p=raw_response.get('provenance',{})
    if (raw_response.get('ok') is not True or data.get('parent')!=parent or data.get('source')!=source
        or p.get('input_binding')!=raw_request['inputBinding'] or p.get('source_binding')!=raw_request['sourceBinding']):
        raise ValueError('v1 DATA current source/parent/request binding mismatch')
    v=c.strict_json(raw_response['text'])
    if v.get('status')!='complete' or not v.get('children'):raise ValueError('v1 DATA not decomposed')
    quotes=[a['quote'] for row in v['children'] for a in row['anchors']]
    cat=catalog(parent,source,quotes);rows=[];proof=[]
    for row in v['children']:
        anchors=[unique_quote(source,a['quote']) for a in row['anchors']]
        expression=row['target_expression']
        # Existing correct declared owner anchors remain proof. Otherwise recover only
        # one exact literal owner line within uniquely recovered source quotation DATA.
        declared=c.anchors(source,row['owner_ranges'])
        owners=[a for a in declared if expression in a['quote']]
        if not owners:
            candidates={a['start']+i for a in anchors for i,line in enumerate(a['quote'].splitlines()) if expression in line}
            if len(candidates)!=1:raise ValueError('v1 owner expression absent or ambiguous; v2 repair required')
            owners=c.anchors(source,[[next(iter(candidates)),next(iter(candidates))]])
        ids=[next(a['id'] for a in cat['entries'] if all(a[k]==r[k] for k in ('start','end','quote'))) for r in anchors]
        owner=owners[0] # each retained declared anchor independently proves the same literal; no semantic location guess
        owner_id=next(a['id'] for a in cat['entries'] if a['start']==owner['start'] and a['end']==owner['end']) if owner['start']==owner['end'] else None
        if owner_id is None:raise ValueError('v1 multi-line owner requires explicit v2 anchor selection')
        rows.append({**{k:copy.deepcopy(row[k]) for k in ('claim','target_expression','conditions','definition')},
                     'anchor_ids':ids,'owner_anchor_id':owner_id,'derivation':'unassessed v1 DATA; normal selected-parent review required'})
        proof.append({'original':copy.deepcopy(row),'resolved_anchors':anchors,'resolved_owner_anchors':owners})
    return {'value':{'status':'complete','reason':v['reason'],'children':rows},'catalog':cat,
            'audit':{'type':'audited-derived-proof/2','original_response_valid':False,'method':'unique-exact-complete-line quotation / continuous literal owner',
                     'source_sha256':source_sha(source),'parent_binding':digest(parent),'raw_request_sha':digest(raw_request),
                     'raw_response_sha':digest(raw_response),'raw_provenance':copy.deepcopy(p),'steps':proof}}

# Task358 P1 input contract `source-lines/1`: the source is sent ONCE as line-numbered text and models cite canonical
# line refs (`L23` / `L23-L25`). HOST resolves complete lines from the bound source, so a model still cannot invent a
# quote; this replaces the per-line anchor catalog (each line quoted twice + ids/hashes, ~4.7x the source).
LINES_FORMAT = 'source-lines/1'
_REF = __import__('re').compile(r'L([1-9][0-9]*)(?:-L([1-9][0-9]*))?')

def numbered(source):
    return ''.join('L%d %s\n' % (i, line) for i, line in enumerate(source.splitlines(), 1))

def canonical_ref(ref):
    """`L7-L7` -> `L7` (same single line, Task361 live); anything else is returned unchanged for strict resolution."""
    m = _REF.fullmatch(ref) if isinstance(ref, str) else None
    return 'L' + m.group(1) if m and m.group(2) and m.group(1) == m.group(2) else ref

def resolve_lines(source, refs):
    """Canonical refs only (`L7`, `L7-L9` with 7<9): unknown/duplicate/out-of-range/non-canonical -> ValueError."""
    if not isinstance(refs, list) or not refs or len(refs) != len(set(refs)):
        raise ValueError('unknown or duplicate SOURCE line ref')
    out = []
    for ref in refs:
        m = _REF.fullmatch(ref) if isinstance(ref, str) else None
        if not m:
            raise ValueError('non-canonical SOURCE line ref')
        start = int(m.group(1)); end = int(m.group(2) or start)
        if m.group(2) and end <= start:
            raise ValueError('non-canonical SOURCE line ref')
        out.append(c.anchors(source, [[start, end]])[0])
    return out
