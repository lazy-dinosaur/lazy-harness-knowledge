"""Task376 stagewise verification runner (stage 0): run ONE pipeline stage on frozen, user-approved inputs.

Baseline = the original V2 pipeline only (`place_write.judge_input` -> `place_write.place_identity`), i.e. the
2026-10-13 plan: judge -> domain/subject top-K + Jev(+none) -> ... No SDK rescue roles are attached (Task376 plan B):
a ports object carrying `domain_review_policy` / `normal_fidelity_policy` is refused.

Carry-over (Task377 D4): a stage starts from a named snapshot (domains + subjects). Only the results of a stage
the user passed are promoted into the next named snapshot. Nothing here calls a provider by itself; the caller
supplies ports (offline fakes in tests, broker-accounted production ports for a separately approved live stage).
"""
import copy
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL

import tempfile
import place_write as w
import upper_grouping as ug
from safety import load, save, digest

VERSION = 'stagewise/1'
FORBIDDEN_PORT_FIELDS = ('domain_review_policy', 'normal_fidelity_policy')
RECORD_KEYS = {'record_id', 'path', 'sha256', 'text'}


def baseline_ports(ports):
    """Refuse rescue roles that bypass the DDD domain criterion (Task374/376)."""
    for field in FORBIDDEN_PORT_FIELDS:
        if getattr(ports, field, None) is not None:
            raise ValueError('baseline forbids SDK rescue role: ' + field)
    return ports


def _need(cond, message):
    if not cond:
        raise ValueError(message)


# ---------------------------------------------------------------- snapshots

def snapshot(name, domains, subjects, *, parent=None, approval=None, db_dump=None, groupings=None):
    """A named carry-over state. `subjects` maps domain name -> subject rows (subject_id/name/description/aliases)."""
    _need(isinstance(name, str) and name, 'snapshot name required')
    _need(all(isinstance(d, dict) and d.get('name') and 'description' in d for d in domains), 'domain rows need name/description')
    value = {'version': VERSION, 'name': name, 'parent': parent, 'approval': approval, 'db_dump': db_dump,
             'domains': [dict(copy.deepcopy(d), aliases=list(d.get('aliases', []))) for d in domains],
             'subjects': {k: copy.deepcopy(v) for k, v in sorted(subjects.items())}}
    if groupings is not None:  # upper-grouping links (link only); absent in older snapshots
        value['groupings'] = copy.deepcopy(groupings)
    value['sha'] = digest({k: v for k, v in value.items() if k != 'sha'})
    return value


def _checked_snapshot(value):
    _need(isinstance(value, dict) and value.get('version') == VERSION, 'unknown snapshot')
    _need(value.get('sha') == digest({k: v for k, v in value.items() if k != 'sha'}), 'snapshot drift')
    return value


def approve(report, approval):
    """Mark a stage report as passed by the user. `approval` is the user's message reference, never inferred."""
    _need(isinstance(approval, str) and approval.strip(), 'explicit user approval reference required')
    _need(report.get('sha') == digest({k: v for k, v in report.items() if k not in ('sha', 'approval')}), 'report drift')
    return dict(copy.deepcopy(report), approval=approval)


def _approved(report, stage):
    _need(isinstance(report, dict) and report.get('stage') == stage, 'input must be a ' + stage + ' report')
    _need(report.get('sha') == digest({k: v for k, v in report.items() if k not in ('sha', 'approval')}), 'report drift')
    _need(isinstance(report.get('approval'), str) and report['approval'].strip(), stage + ' report not passed by the user')
    return report


def _sealed(value):
    value['sha'] = digest({k: v for k, v in value.items() if k not in ('sha', 'approval')})
    return value


# ---------------------------------------------------------------- stage 1: extraction

def run_extract(ports, records, *, host, out=None):
    """Stage 1. Same call as place_write.run's judge step (origin v1-document, migration=True)."""
    baseline_ports(ports)
    rows = []
    for record in records:
        _need(isinstance(record, dict) and set(record) == RECORD_KEYS, 'record needs record_id/path/sha256/text')
        items = w.judge_input(ports, record['record_id'], record['text'], origin='v1-document', migration=True)
        for index, item in enumerate(items):
            ident = str(uuid5(NAMESPACE_URL, digest([host, record['record_id'], record['sha256'], index, item, 'place-write-v2'])))
            text = record['text']
            unit = dict(copy.deepcopy(item), host_id=host, unit_id=ident, record_id=record['record_id'], index=index)
            if item.get('source_ranges'):
                unit['source_text'] = '\n'.join('\n'.join(text.splitlines()[a - 1:b]) for a, b in item['source_ranges'])
            flags = [k for k in ('judge_partial', 'range_invalid', 'target_ungrounded', 'content_ungrounded') if item.get(k)]
            rows.append({'record_id': record['record_id'], 'index': index, 'unit': unit, 'flags': flags})
    report = _sealed({'version': VERSION, 'stage': 'extract', 'host': host,
                      'records': [{k: r[k] for k in ('record_id', 'path', 'sha256')} for r in records],
                      'sources': {r['record_id']: r['text'] for r in records}, 'rows': rows})
    if out is not None:
        save(Path(out) / 'extract.json', report)
    return report


# ---------------------------------------------------------------- stages 2-3: domain and subject placement

def run_place(ports, extract_report, base, *, out=None, top_k=5, on_row=None, skip_units=None, second_pass=False, upper_grouping=False,
              one_home=False, resume_rows=None, warrant=False):
    """Stages 2 (domain) and 3 (subject) from a user-passed extract report and a named snapshot.

    Mirrors place_write.run: domain first, then subject inside the chosen domain; successful choices update the
    in-run catalogs exactly as the pipeline does. HOLDs stay HOLDs (no rescue path).
    Task380c: `on_row(index, row, domains, subjects)` is called after every unit so a caller can persist progress;
    a run stopped midway keeps every judged unit (identical requests are answered from the ledger on rerun).
    resume_rows ({index: {'row': saved_row, 'groupings': [...]}}, #24 runner defect): first-pass rows saved by an earlier
    stopped run are reused without any new call; their catalog effects (domains, subjects, counts) are replayed in order
    and the groupings saved with the last reused row are restored. Second-pass rows are not reused.
    warrant (user option A1, "추천대로 가봐 그러면"): a unit held only because an existing domain partly covers its new-domain
    proposal (coverage .3-.7) records that proposal; when a second unit proposes the same name (exact, normalized), the
    domain is created at that save and every unit that proposed it is placed into it in the same step (no later job).
    second_pass (S003 review, user "추천대로 진행해줘"): after all units, units held at the domain or subject step are placed
    ONCE more against the catalog grown during the run (a shared-kernel domain created late can then take the earlier
    units). Held rows keep their first-pass hold as `first_hold`; extract-flagged and carried units are never retried.
    """
    baseline_ports(ports)
    _approved(extract_report, 'extract')
    base = _checked_snapshot(base)
    domains = copy.deepcopy(base['domains'])
    subjects = copy.deepcopy(base['subjects'])
    groupings = copy.deepcopy(base.get('groupings') or [])  # upper grouping (user-accepted option A), link only
    counts = {}  # (domain, subject) -> placed units in this run, for the one-home tie break
    rows = []

    def commit(placement, held):
        for kind in ('domain', 'subject'):
            chosen = (placement.get(kind) or {}).get('chosen')
            if not chosen or held and held['where'] == kind:
                continue
            catalog = domains if kind == 'domain' else subjects.setdefault(placement['domain']['chosen']['name'], [])
            key = (lambda r: r.get('subject_id') == chosen.get('subject_id')) if kind == 'subject' else (lambda r: r['name'] == chosen['name'])
            found = next((i for i, r in enumerate(catalog) if key(r)), None)
            if found is None:
                catalog.append(copy.deepcopy(chosen))
            else:
                catalog[found] = copy.deepcopy(chosen)
        if not held and (placement.get('domain') or {}).get('chosen') and (placement.get('subject') or {}).get('chosen'):
            key = (placement['domain']['chosen']['name'], placement['subject']['chosen']['name'])
            counts[key] = counts.get(key, 0) + 1

    pending = {}  # normalized proposed domain name -> [row positions held in the coverage band]
    blocked = {}  # normalized proposed domain name -> existing domains it covers or overlaps (warrant refused)
    by_index = {r['index']: r for r in extract_report['rows']}

    def maybe_warrant(pos):
        done = rows[pos]; held = done.get('held') or {}
        if held.get('where') != 'domain' or 'coverage is not clearly no' not in held.get('reason', ''):
            return
        dtrace = (done.get('trace') or {}).get('domain') or {}
        proposal = dtrace.get('proposal') or {}
        name = proposal.get('name')
        if not isinstance(name, str) or not name.strip() or any(w._norm(d['name']) == w._norm(name) for d in domains):
            return
        if not (dtrace.get('domain_criterion') or {}).get('pass'):
            return  # only proposals that passed the business-area criterion count as warrant
        if w._norm(name) in blocked:
            done['warrant'] = {'name': name, 'blocked_by': blocked[w._norm(name)]}
            return
        for other in pending.values():  # a unit counts once, under its latest proposal
            if pos in other:
                other.remove(pos)
        members = pending.setdefault(w._norm(name), [])
        members.append(pos)
        if len(members) < 2:
            done['warrant'] = {'name': name, 'pending': 1}
            return
        # B (user "그렇게하자 그러면"): the same no-catch-all / overlap gates as normal creation, checked once.
        gate = {}
        covered = w.covering_domains({'name': name.strip(), 'description': (proposal.get('description') or '').strip()},
                                     by_index[done['index']]['unit'], domains, ports, gate)
        if covered:
            blocked[w._norm(name)] = [o['name'] for o, _ in covered]
            for p in members:
                rows[p]['warrant'] = {'name': name, 'blocked_by': blocked[w._norm(name)], 'gate': gate}
                notify(p)
            del pending[w._norm(name)]
            return
        domain = {'name': name.strip(), 'description': (proposal.get('description') or '').strip(), 'aliases': [],
                  'source_concept': proposal.get('source_concept')}
        domain['vector'] = w.checked_vectors(ports.encode([w.identity_text('domain', domain)]), 1)[0]
        info = {'name': domain['name'], 'members': [rows[p]['index'] for p in members], 'gate': gate}
        for k, p in enumerate(list(members)):
            first_hold = rows[p].get('held')
            again = place_unit(by_index[rows[p]['index']], forced=(domain, k == 0, info))
            again['warrant'] = dict(info, first_hold=first_hold)
            rows[p] = again
            notify(p)
        del pending[w._norm(name)]

    def notify(index):
        if on_row is None:
            return
        import inspect
        if len(inspect.signature(on_row).parameters) >= 5:
            on_row(index, rows[index], domains, subjects, groupings)
        else:
            on_row(index, rows[index], domains, subjects)

    def place_unit(row, forced=None):
        unit = row['unit']; placement = {}
        text = extract_report['sources'][row['record_id']]
        held = None
        home = w.cross_domain_home(unit, domains, subjects, ports, counts=counts) if one_home and not forced else None
        for kind in ('domain', 'subject'):
            if kind == 'domain' and forced:
                # warrant: the domain created from repeated proposals; only the subject step runs
                placement['domain'] = {'chosen': copy.deepcopy(forced[0]), 'new': forced[1], 'warrant': forced[2]}
                continue
            if kind == 'domain' and home and home.get('domain'):
                # one home per target: the existing subject's domain, no new domain search
                placement['domain'] = {'chosen': copy.deepcopy(home['domain']), 'new': False, 'one_home': home['trace']}
                continue
            catalog = domains if kind == 'domain' else subjects.get(placement['domain']['chosen']['name'], [])
            try:
                placement[kind] = w.place_identity(kind, unit, placement.get('domain', {}).get('chosen'), catalog, ports, top_k, document=text)
            except ValueError as exc:
                placement[kind] = getattr(exc, 'trace', {}) or {}
                held = {'where': kind, 'reason': str(exc)}
                break
        commit(placement, held)
        grouping = None
        if upper_grouping:
            dtrace = placement.get('domain') or {}
            if dtrace.get('new') and dtrace.get('chosen') and not (held and held['where'] == 'domain'):
                grouping = ug.attach_new_domain(dtrace['chosen'], groupings, domains, ports)
            elif dtrace.get('umbrella_candidate'):
                grouping = ug.from_umbrella(dtrace['umbrella_candidate'], groupings, domains, ports)
        out_row = {'unit_id': unit['unit_id'], 'record_id': row['record_id'], 'index': row['index'],
                   'content': unit['content'], 'target_expression': unit['target_expression'],
                   'domain': summary(placement.get('domain')), 'subject': summary(placement.get('subject')),
                   'held': held, 'trace': placement}
        if grouping is not None:
            out_row['grouping'] = grouping
        if home is not None:
            out_row['one_home'] = home['trace']
        return out_row

    for row in extract_report['rows']:
        unit = row['unit']
        reason = (skip_units or {}).get(unit['unit_id'])
        saved = (resume_rows or {}).get(row['index'])
        if saved and saved['row'].get('unit_id') == unit['unit_id'] and saved['row'].get('pass') != 2:
            done = copy.deepcopy(saved['row'])
            if 'trace' in done:
                commit(done['trace'], done.get('held'))
            if saved.get('groupings') is not None:
                groupings[:] = copy.deepcopy(saved['groupings'])
            rows.append(done)
            if warrant:
                maybe_warrant(len(rows) - 1)  # re-register a reused row's pending proposal (#24 gap)
            continue
        if row['flags'] or reason:
            # Task382: a unit whose paid call is carried as uncertain is held without any new call (no resend).
            rows.append({'unit_id': unit['unit_id'], 'record_id': row['record_id'], 'index': row['index'],
                         'held': {'where': 'carried' if reason else 'extract', 'reason': reason or ','.join(row['flags'])}})
        else:
            rows.append(place_unit(row))
            if warrant:
                maybe_warrant(len(rows) - 1)
        notify(len(rows) - 1)
    if second_pass:
        by_id = {r['unit']['unit_id']: r for r in extract_report['rows']}
        for i, done in enumerate(rows):
            if not done.get('held') or done['held']['where'] not in ('domain', 'subject'):
                continue
            again = place_unit(by_id[done['unit_id']])
            again['pass'] = 2; again['first_hold'] = done['held']
            rows[i] = again
            if warrant:
                maybe_warrant(i)  # the warrant also counts second-pass holds (#24 gap)
            notify(i)
    report = _sealed({'version': VERSION, 'stage': 'place', 'snapshot': base['name'], 'snapshot_sha': base['sha'],
                      'extract_sha': extract_report['sha'], 'rows': rows, 'second_pass': bool(second_pass), **({'one_home': True} if one_home else {}), **({'warrant': True} if warrant else {}),
                      'domains_after': domains, 'subjects_after': subjects,
                      # Task385: upper-grouping candidates (link layer; never a same-level domain, never a copy of knowledge)
                      'umbrella_candidates': [dict(r['domain']['umbrella'], unit_id=r['unit_id']) for r in rows if r.get('domain') and r['domain'].get('umbrella')],
                      **({'groupings_after': groupings} if upper_grouping else {})})
    if out is not None:
        save(Path(out) / 'place.json', report)
    return report


def summary(trace):
    """Human-review view of one placement trace (stage 2/3 checklists)."""
    if not trace:
        return None
    chosen = trace.get('chosen') or {}
    searches = [{'query_kind': s.get('query_kind'), 'choice': s.get('choice'),
                 'probabilities': s.get('probabilities')} for s in trace.get('searches', [])]
    return {'chosen': chosen.get('name'), 'new': trace.get('new'), 'searches': searches,
            'coverage': trace.get('domain_coverage_checks'), 'creation_choice': trace.get('domain_creation_choice'),
            'membership': trace.get('ambiguous_domain_membership'), 'overlap': trace.get('domain_overlap_checks'),
            'umbrella': trace.get('umbrella_candidate'), 'umbrella_child': trace.get('umbrella_child'),
            'uses': trace.get('context_map_uses'), 'same_meaning': trace.get('ambiguous_same_meaning'),
            'ddd_checks': trace.get('ddd_checks')}


def promote(base, place_report, name):
    """Next named snapshot from a user-passed place report (Task377 D4)."""
    _approved(place_report, 'place')
    base = _checked_snapshot(base)
    _need(place_report['snapshot_sha'] == base['sha'], 'place report was not produced from this snapshot')
    return snapshot(name, place_report['domains_after'], place_report['subjects_after'],
                    parent=base['name'], approval=place_report['approval'], db_dump=base.get('db_dump'),
                    groupings=place_report.get('groupings_after', base.get('groupings')))


# ---------------------------------------------------------------- stages 4-5: writing and Q1/Q2 review

def _home(extract_report, root=None):
    """validate_fact reads <home>/v1/<path>; materialize the frozen extract sources (never the live host)."""
    # Evidence locators are repository-relative, so the scratch home lives inside the runner tree (as the tests do).
    home = Path(root) if root is not None else Path(tempfile.mkdtemp(prefix='stagewise-home-', dir=Path(w.__file__).parent))
    for record in extract_report['records']:
        path = home / 'v1' / record['path']; path.parent.mkdir(parents=True, exist_ok=True)
        text = extract_report['sources'][record['record_id']]
        _need(__import__('hashlib').sha256(text.encode()).hexdigest() == record['sha256'], 'extract source drift')
        path.write_text(text)
    return home


def _record(extract_report, rid):
    return next(r for r in extract_report['records'] if r['record_id'] == rid)


def run_write(ports, place_report, extract_report, *, out=None):
    """Stage 4: first writing of each placed unit, then Jev kind + source-modality review (place_write.run do_write)."""
    baseline_ports(ports)
    _approved(extract_report, 'extract'); _approved(place_report, 'place')
    _need(place_report['extract_sha'] == extract_report['sha'], 'place report is not from this extract report')
    units = {r['unit']['unit_id']: r['unit'] for r in extract_report['rows']}
    rows = []
    for row in place_report['rows']:
        if row.get('held'):
            rows.append({'unit_id': row['unit_id'], 'held': row['held']}); continue
        unit = units[row['unit_id']]; placement = row['trace']; text = extract_report['sources'][row['record_id']]
        packet = copy.deepcopy({'source': text, 'unit': unit, 'chosen_domain': placement['domain']['chosen'], 'chosen_subject': placement['subject']['chosen']})
        try:
            answer = ports.writer('write', packet) if ports.writer else ports.model('write-' + unit['unit_id'], packet, w.WRITE_TASK)
            answer.pop('kind', None)
            kind = w.fact_kind(ports, unit, answer, source=text); answer['kind'] = kind['choice']
            written = {'answer': answer, 'kind': kind}
        except ValueError as exc:
            written = {'answer': {}, 'error': str(exc)}
        modality = (written['answer'].get('_source_modality') or {})
        rows.append({'unit_id': row['unit_id'], 'record_id': row['record_id'], 'domain': row['domain']['chosen'],
                     'subject': row['subject']['chosen'], 'content': unit['content'], 'written': written,
                     'fact': written['answer'].get('fact'), 'kind': written['answer'].get('kind'),
                     'modality': {'accepted': modality.get('accepted'), 'mode': (modality.get('source_intent') or {}).get('mode'),
                                  'fidelity': (modality.get('fidelity') or {}).get('accepted')}, 'held': None})
    report = _sealed({'version': VERSION, 'stage': 'write', 'place_sha': place_report['sha'], 'extract_sha': extract_report['sha'], 'rows': rows})
    if out is not None:
        save(Path(out) / 'write.json', report)
    return report


def run_ground(ports, write_report, place_report, extract_report, *, out=None, home=None):
    """Stage 5: whole-document Q1/Q2 (source_grounding.check_many) with the pipeline's single rewrite (do_ground)."""
    from source_grounding import check_many, repair_citations
    baseline_ports(ports)
    _approved(extract_report, 'extract'); _approved(place_report, 'place'); _approved(write_report, 'write')
    _need(write_report['place_sha'] == place_report['sha'] and write_report['extract_sha'] == extract_report['sha'], 'write report chain drift')
    home = _home(extract_report, home)
    units = {r['unit']['unit_id']: r['unit'] for r in extract_report['rows']}
    places = {r['unit_id']: r for r in place_report['rows']}
    rows = []
    for row in write_report['rows']:
        if row.get('held'):
            rows.append({'unit_id': row['unit_id'], 'held': row['held']}); continue
        unit = units[row['unit_id']]; placement = places[row['unit_id']]['trace']; text = extract_report['sources'][row['record_id']]
        record = _record(extract_report, row['record_id']); written = row['written']

        def check(answer, original=None):
            try:
                f = w.validate_fact(record, home, unit, placement, copy.deepcopy(answer), original)
            except (ValueError, TypeError, KeyError) as exc:
                return None, {'errors': [{'code': 'E_WRITE_FORM', 'detail': str(exc)}]}
            q = check_many([f], text, ports.ground or ports.ask, check_identity=False)[0]
            if not q['errors']:
                repair_citations(f, q, text)
            return f, q
        if not written.get('answer'):
            final = {'errors': [{'code': 'E_WRITE', 'detail': written.get('error')}]}
            rows.append({'unit_id': row['unit_id'], 'fact': None, 'rewritten': False, 'final': final, 'held': final['errors']}); continue
        fact, initial = check(written['answer'])
        result = {'unit_id': row['unit_id'], 'record_id': row['record_id'], 'initial': initial, 'rewritten': False, 'final': initial, 'fact': fact}
        if initial['errors']:
            try:
                packet = copy.deepcopy({'source': text, 'unit': unit, 'chosen_domain': placement['domain']['chosen'], 'chosen_subject': placement['subject']['chosen'],
                                        'original_fields_not_evidence': written['answer'], 'errors': initial['errors']})
                delta = ports.writer('rewrite', packet) if ports.writer else ports.model('rewrite-' + unit['unit_id'], packet,
                    w.WRITE_TASK + ' 이것이 유일한 재작성 시도다. 원문으로 다시 쓰되 주어 불변. 바뀐 필드만 JSON 허용.')
                delta.pop('kind', None)
                merged = {**written['answer'], **delta}
                kind = w.fact_kind(ports, unit, merged, source=text); delta['kind'] = kind['choice']
                delta['_source_modality'] = merged['_source_modality']
                fact, final = check(delta, written['answer'])
            except ValueError as exc:
                delta = {}; fact = None; final = {'errors': [{'code': 'E_REWRITE', 'detail': str(exc)}]}
            result.update(rewritten=True, rewrite=delta, final=final, fact=fact)
        result['held'] = result['final']['errors'] or None
        rows.append(result)
    report = _sealed({'version': VERSION, 'stage': 'ground', 'write_sha': write_report['sha'], 'rows': rows})
    if out is not None:
        save(Path(out) / 'ground.json', report)
    return report
