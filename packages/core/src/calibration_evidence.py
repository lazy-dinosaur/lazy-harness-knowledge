"""Only judged text + typed paid labels. No labels inferred from current subject membership.
Legacy HTTP journals lack requests: immutable DB receipts/snapshot fingerprints fill that gap;
unrecoverable pair requests and ambiguous multi-candidate positive excerpts are excluded.
"""
import hashlib
import json
from collections import Counter, defaultdict
from safety import Stop, digest, load
from local_db import HOST


def probability(answer):
    value = answer.get('noul') if isinstance(answer,dict) else None
    return float(value) if type(value) in (int,float) and 0 <= value <= 1 else None


def pair(family, left, right, same, source, roles=('query','query')):
    return {'family':family,'left':left,'right':right,'same':same,'roles':list(roles),'source':source}


def receipt_pairs(packet, answers, known, source):
    state = packet.get('state',{})
    left = state.get('candidate_fact')
    name = 'is_new' if 'is_new' in answers else 'differs_from_target'
    value = probability(answers.get(name))
    if not isinstance(left,str) or value is None or .1 < value < .9:
        return []
    excerpt = state.get('existing_records_excerpt' if name=='is_new' else 'target_excerpt','')
    if not isinstance(excerpt,str) or not excerpt or excerpt.startswith('[text fallback:'):
        return []
    # A positive judgment against several candidates does NOT identify which one matched.
    candidates = excerpt.splitlines()
    if value <= .1 and len(candidates) != 1:
        return []
    out = []
    for right in candidates:
        if right not in known or len(right) >= 600 or right == left:
            continue
        out.append(pair('fragment',left,right,value <= .1,source,('query','passage')))
    return out


def merge_pairs(kind, snapshot, verdict, source):
    from subject_dict import core
    from subject_merge import ambiguity
    luna, value = verdict.get('luna'),verdict.get('jev')
    if not isinstance(luna,dict) or type(value) not in (int,float) or not 0 <= value <= 1:
        return []
    same = ambiguity(luna,value)
    refs = luna.get('evidence')
    supported = isinstance(refs,list) and bool(refs) and all(isinstance(r,dict) and r.get('locator') and r.get('quote') for r in refs)
    different = luna.get('relation') == 'different' and value <= .2 and bool(luna.get('description_comparison')) and supported
    if not same and not different:
        return []
    if kind=='subject':
        subjects = snapshot.get('subjects',[])
        if not isinstance(subjects,list) or len(subjects) != 2:
            return []
        # Both registration core names and maintenance descriptions are actual subject-vector surfaces.
        surfaces = [[core(s['name']),s.get('description')] for s in subjects]
        return [pair('subject',a,b,same,source) for a in dict.fromkeys(surfaces[0]) for b in dict.fromkeys(surfaces[1]) if a and b and a!=b]
    if kind=='domain':
        domains = snapshot.get('domains',{})
        if not isinstance(domains,dict) or len(domains) != 2:
            return []
        texts = [r.get('description') or name for name,r in domains.items()]
        return [pair('domain',*texts,same,source)] if texts[0]!=texts[1] else []
    return []


def collect(cur, home, paid):
    cur.execute('''select r.receipt_id::text,r.packet,r.answers,r.jev_model_actual
        from knowledge.check_receipt r join knowledge.ledger_entry e using(entry_id)
        where e.host_id=%s order by r.receipt_id''',(HOST,))
    receipts = cur.fetchall()
    cur.execute('select text from knowledge.fragment where host_id=%s',(HOST,))
    known = {r[0] for r in cur.fetchall()}
    cur.execute("select h.snapshot->>'text' from knowledge.fragment_history h join knowledge.fragment f on f.id=h.fragment_id where f.host_id=%s",(HOST,))
    known.update(r[0] for r in cur.fetchall())
    known.update(p.get('state',{}).get('candidate_fact') for _,p,_,_ in receipts)
    paid_answers = set()
    requests = []
    audit = Counter()
    for row in {r['id']:r for r in paid.ledger.rows}.values():
        if row['state']!='done' or row['step'] not in ('absorption','maintenance'):
            continue
        response = load(home/'state'/row['result'])
        if digest(response)!=row['sha']:
            raise Stop('calibration paid response drift')
        if isinstance(response.get('answers'),dict):
            paid_answers.add(digest(response['answers']))
            if row.get('request'):
                requests.append((row,response))
            else:
                audit['legacy_paid_answers_without_request'] += 1
    pairs = []
    for ident,packet,answers,model in receipts:
        if model=='offline-fixture' or digest(answers) not in paid_answers:
            audit['unlinked_or_fixture_receipts'] += 1
            continue
        found = receipt_pairs(packet,answers,known,{'receipt_id':ident,'packet_sha':digest(packet)})
        pairs.extend(found)
        if not found: audit['ambiguous_or_unusable_receipts'] += 1
    # New journals keep exact request state, so record-time subject pair labels survive rollback too.
    from subject_dict import SAME_Q, core
    for row,response in requests:
        body = row['request'].get('body',{})
        state = body.get('state',{})
        name = state.get('new',{}).get('name')
        for item in state.get('items',[]):
            index = item.get('i'); question = body.get('questions',{}).get('m'+str(index),{})
            if not name or question.get('instructions') != SAME_Q.format(i=index):
                continue
            value = probability(response['answers'].get('m'+str(index)))
            if value is None or .1 < value < .9:
                continue
            try: candidate = json.loads(item['text'])
            except (ValueError,TypeError,KeyError): continue
            if candidate.get('name'):
                pairs.append(pair('subject',core(name),core(candidate['name']),value>=.9,{'paid_id':row['id']}))
    snapshots = {}
    for path in (home/'state/packets').glob('maintenance-*/input.json'):
        snapshot = load(path)
        fingerprint = hashlib.sha256(json.dumps(snapshot,sort_keys=True,default=str).encode()).hexdigest()
        snapshots[fingerprint] = snapshot
    cur.execute('select id,kind,fragment_fingerprint,verdict from knowledge.merge_judgement where host_id=%s order by id',(HOST,))
    for ident,kind,fingerprint,verdict in cur.fetchall():
        snapshot = snapshots.get(fingerprint)
        if snapshot is None:
            audit['merge_snapshot_missing'] += 1
            continue
        found = merge_pairs(kind,snapshot,verdict,{'merge_judgement':ident,'snapshot_sha':fingerprint})
        pairs.extend(found)
        if not found: audit['held_or_unusable_merges'] += 1
    grouped = defaultdict(list)
    for row in pairs:
        grouped[digest([row['family'],row['left'],row['right'],row['roles']])].append(row)
    out = []
    for rows in grouped.values():
        if len({r['same'] for r in rows}) != 1:
            audit['contested_pairs'] += len(rows)
        else:
            out.append({**rows[0],'sources':[r['source'] for r in rows]})
    return sorted(out,key=digest),dict(audit)
