"""Upper grouping — the DDD subdomain-classification layer above domains (link only, never a copy of knowledge).

User-accepted design (2026-11-07, option A "붙이는 건 바로, 만드는 건 두 개부터, 고치는 건 P3가 모아서"):
- when a new domain is created it is attached at once to an existing grouping it belongs to;
- a grouping is created only when two or more ungrouped domains belong together (no one-member grouping), or from a
  new-domain proposal that covers two or more existing domains (Task385 umbrella candidate);
- renaming, merging and splitting groupings is the organizing role's batch work and is never done here.
The 0.7 threshold mirrors the other membership checks and is unmeasured for groupings.
"""
import place_write as w
from subject_contract import short_name_errors

THRESHOLD = .7
MEMBER_QUESTION = (
    'DDD 전략 설계의 하위 도메인 분류(여러 경계 컨텍스트를 묶는 탐색용 상위 분류)를 판단한다. '
    'proposed는 새 도메인(경계 컨텍스트)이고 candidate는 기존 상위 분류다. candidate.example에 그 분류에 묶인 도메인들이 있다. '
    'proposed 도메인이 그 도메인들과 같은 업무군으로 이 상위 분류에 함께 속하는가? '
    '한쪽이 다른 쪽의 공용 기능을 가져다 쓰기만 하는 관계, 문장 속 언급, 같은 서비스 안에 있다는 이유만으로는 아니다.')
PAIR_QUESTION = (
    'DDD 전략 설계의 하위 도메인 분류(여러 경계 컨텍스트를 묶는 탐색용 상위 분류)를 판단한다. '
    'proposed와 candidate는 서로 다른 두 도메인(경계 컨텍스트)이다. 두 도메인이 같은 업무군으로 하나의 상위 분류에 함께 묶이는가? '
    '한쪽이 다른 쪽의 공용 기능을 가져다 쓰기만 하는 관계, 문장 속 언급, 같은 서비스 안에 있다는 이유만으로는 아니다.')


def _row(domain):
    return {'name': domain['name'], 'description': domain.get('description', '')}


def group_of(name, groupings):
    return next((g for g in groupings if name in g['members']), None)


def _grouped(groupings):
    return {m for g in groupings for m in g['members']}


def name_error(proposal, groupings, catalog):
    """Code-owned checks on a proposed grouping name; the model only proposes."""
    if not isinstance(proposal, dict):
        return 'E_GROUP_NAME: proposal required'
    name, description = proposal.get('name'), proposal.get('description')
    if short_name_errors(name, area=True):
        return 'E_GROUP_NAME: one word, one concept'
    if not isinstance(description, str) or not description.strip() or len(description) > 300 or not 1 <= len(description.strip().splitlines()) <= 2:
        return 'E_GROUP_NAME: 1-2 line description required'
    taken = {w._norm(d['name']) for d in catalog} | {w._norm(g['name']) for g in groupings}
    if w._norm(name) in taken:
        return 'E_GROUP_NAME: name equals an existing domain or grouping'
    language = w.area_language_error(name, catalog)
    if language:
        return language
    return None


TOP_K = 5


def _vector(ports, group):
    # Searchable like domains and subjects (user: every layer has a description that vector search can hit).
    return w.checked_vectors(ports.encode([w.identity_text('domain', group)]), 1)[0]


def _shortlist(domain, groupings, ports):
    """Vector top-K groupings for a domain (all of them when few or when vectors are missing)."""
    if len(groupings) <= TOP_K or any('vector' not in g for g in groupings):
        return list(groupings)
    from subject_dict import cos
    query = domain.get('vector') or w.checked_vectors(ports.encode([w.identity_text('domain', domain)]), 1)[0]
    return sorted(groupings, key=lambda g: (-cos(query, g['vector']), g['name']))[:TOP_K]


def attach_new_domain(domain, groupings, catalog, ports, *, threshold=THRESHOLD):
    """Attach a newly created domain to a grouping, or create a grouping with ungrouped partners. Mutates groupings."""
    event = {'domain': domain['name'], 'action': 'none'}
    if group_of(domain['name'], groupings):
        event['action'] = 'already-grouped'
        return event
    if groupings:
        short = _shortlist(domain, groupings, ports)
        candidates = [{'name': g['name'], 'description': g['description'], 'example': '묶인 도메인: ' + ', '.join(g['members'])}
                      for g in short]
        scores = w.score_many(ports, {'proposed': _row(domain)}, candidates, MEMBER_QUESTION)
        event['member_checks'] = [{'group': g['name'], 'score': s} for g, s in zip(short, scores)]
        best = max(range(len(short)), key=lambda i: scores[i])
        if scores[best] >= threshold:
            short[best]['members'].append(domain['name'])
            event.update(action='attached', group=short[best]['name'])
            return event
    grouped = _grouped(groupings)
    others = [d for d in catalog if d['name'] != domain['name'] and d['name'] not in grouped]
    if not others:
        return event
    scores = w.score_many(ports, {'proposed': _row(domain)}, [_row(d) for d in others], PAIR_QUESTION)
    event['pair_checks'] = [{'domain': d['name'], 'score': s} for d, s in zip(others, scores)]
    partners = [d for d, s in zip(others, scores) if s >= threshold]
    if not partners:
        return event
    members = [domain] + partners
    packet = {'domains': [_row(d) for d in members], 'existing_groups': [g['name'] for g in groupings]}
    try:
        named = w.role_call(ports, 'group-name', 'group-' + w.digest(packet)[:24], packet, 'group-name')
    except ValueError as exc:
        event.update(action='naming-failed', reason=str(exc))
        return event
    error = name_error(named, groupings, catalog)
    if error:
        event.update(action='naming-rejected', reason=error, proposal=named)
        return event
    group = {'name': named['name'].strip(), 'description': named['description'].strip(),
             'members': [d['name'] for d in members], 'origin': 'pair'}
    group['vector'] = _vector(ports, group)
    groupings.append(group)
    event.update(action='created', group=group['name'], members=list(group['members']))
    return event


def from_umbrella(umbrella, groupings, catalog, ports=None):
    """A new-domain proposal that covers existing domains (Task385) becomes a grouping over them. Mutates groupings."""
    covered = [c['domain'] for c in umbrella.get('covers', []) if any(d['name'] == c['domain'] for d in catalog)]
    event = {'umbrella': umbrella.get('name'), 'covers': covered, 'action': 'none'}
    same = next((g for g in groupings if w._norm(g['name']) == w._norm(umbrella.get('name', ''))), None)
    grouped = _grouped(groupings)
    free = [n for n in covered if n not in grouped]
    if same is not None:
        same['members'].extend(free)
        event.update(action='attached' if free else 'already-grouped', group=same['name'], added=free)
        return event
    if len(free) < 2:
        event.update(action='kept-as-candidate', reason='a grouping needs two or more ungrouped domains')
        return event
    error = name_error(umbrella, groupings, catalog)
    if error:
        event.update(action='naming-rejected', reason=error)
        return event
    group = {'name': umbrella['name'].strip(), 'description': umbrella['description'].strip(), 'members': free, 'origin': 'umbrella'}
    if ports is not None:
        group['vector'] = _vector(ports, group)
    groupings.append(group)
    event.update(action='created', group=group['name'], members=list(free))
    return event
