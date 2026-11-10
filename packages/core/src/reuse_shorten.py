"""Offline, Main-only import of exact-key shortening receipts (not generated facts/DB).
Archive is retained in place. Historical subscription usage is reported separately.
"""
import sys
from pathlib import Path
from safety import RUNNER, load, save, digest, Stop, Ledger
sys.path.insert(0, str(RUNNER))


def reuse(plan, home, archive):
    home, archive = Path(home).resolve(), Path(archive).resolve()
    if not archive.is_relative_to(home) or archive == home/'state':
        raise Stop('shorten archive must be preserved inside this scope')
    if any(p.is_symlink() for p in archive.rglob('*')): raise Stop('archive symlink forbidden')
    old_plan = load(archive/'plan.json')
    ledger = Ledger(archive/'paid.jsonl', digest(old_plan), old_plan['caps'])
    latest = {r['id']:r for r in ledger.rows}
    if any(r['state'] not in ('done','released') for r in latest.values()):
        raise Stop('uncertain paid effects; Main must resolve before rebuild')
    frozen = load(archive/'subject-shorten/input.json')
    if frozen['binding'] != digest(old_plan): raise Stop('shorten archive binding drift')
    if load(home/'state/plan.json') != plan: raise Stop('target plan drift')
    count = 0
    for record in plan['records']:
        rid = record['record_id']
        old = frozen['records'].get(rid)
        current = load(home/'state/generated'/(rid+'.json'))
        if old != current: continue  # latest pilot-feature may have changed; never force reuse
        path = archive/'subject-shorten'/(rid+'.json')
        if not path.exists(): continue
        receipt = load(path)
        target = home/'state/subject-shorten-cache'/(rid+'-'+receipt['input_sha']+'.json')
        value = {**receipt, 'reused_from':str(path), 'historical_subscription':True}
        if target.exists() and load(target) != value: raise Stop('shorten cache collision')
        save(target, value); count += 1
    q1 = reuse_q1(plan, home, archive)
    return {'cached_receipts':count, 'cached_q1_answers':q1,
            'reuse':'exact shorten input_sha and byte-identical Q1 packets only; historical usage separate', 'paid_calls':0}


def reuse_q1(plan, home, archive):
    """Carry verified raw Q1 answers, never v4 Q2/name/ground completion decisions."""
    from source_grounding import SENTENCE_QUESTION, Q1_VERSION
    count = 0
    for record in plan['records']:
        rid = record['record_id']
        for path in (archive/'subject-ground/judgements'/rid).glob('*.json'):
            saved = load(path)
            packet = saved.get('input')
            if not isinstance(packet, list) or len(packet)!=3 or packet[2]!=SENTENCE_QUESTION: continue
            if packet[0].get('grounding_version') != Q1_VERSION: continue
            if path.stem != digest(packet) or saved.get('scores_sha') != digest(saved.get('scores')):
                raise Stop('Q1 archive judgement drift')
            target = home/'state/subject-ground/judgements'/rid/path.name
            value = {**saved, 'reused_from':str(path), 'historical_paid':True}
            if target.exists() and load(target) != value: raise Stop('Q1 cache collision')
            save(target, value); count += 1
    return count


if __name__ == '__main__':
    import argparse, json
    from scopes import scope_home
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=['pilot-area-10','pilot-area'], required=True)
    parser.add_argument('--from-state', type=Path, required=True)
    args = parser.parse_args()
    home = scope_home(args.scope)
    print(json.dumps(reuse(load(home/'plan.json'), home, args.from_state), ensure_ascii=False, indent=2))
