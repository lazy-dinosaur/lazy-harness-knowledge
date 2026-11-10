"""Mechanical migration provenance; document authority is not model confidence."""
import re
from pathlib import PurePosixPath
from collections import Counter

CANONICAL = {'decisions', 'spec', 'domain', 'behavior', 'ssot', 'tests'}
# Retired canon is documentary evidence of its own history, not permission to revive it.
CONFIRMED = {'active', 'accepted', 'confirmed', 'user-confirmed', 'verified', 'implemented-and-validated',
             'implemented', 'deprecated', 'superseded', 'reverted'}

def classify(path, text):
    parts = PurePosixPath(path).parts
    folder = next((p for p in parts if p in CANONICAL | {'planning', 'knowledge'}), parts[0] if parts else '')
    digest = re.search(r'(?ms)^## Rule digest\s*\n(.*?)(?=^## |\Z)', text)
    section = digest.group(1) if digest else text
    match = re.search(r'(?im)^\s*[-*]?\s*Status:\s*([^\n]+)', section)
    if not match:
        match = re.search(r'<[\w-]+\b[^>]*\bstatus=["\']([^"\']+)', section)
    status = match.group(1).strip().lower() if match else 'missing'
    token = re.split(r'[ ;,(—]', status)[0]
    confirmed = token in CONFIRMED or token.startswith('active-')
    source = 'official_doc' if folder in CANONICAL and confirmed else 'ai_inference'
    return {'folder': folder, 'status': status, 'evidence_source': source,
            'reason': 'confirmed canonical record' if source == 'official_doc' else 'noncanonical or unconfirmed status; P08/P09 retained'}

def inventory(records, root):
    counts = Counter()
    rows = []
    for record in records:
        result = classify(record['path'], (root / record['path']).read_text())
        rows.append({'record_id':record['record_id'], 'path':record['path'], **result})
        key = (result['folder'], result['status'], result['evidence_source'])
        counts[key, 'all'] += 1
        counts[key, 'pilot'] += bool(record.get('pilot'))
    keys = sorted({key for key, _ in counts})
    return {'total':len(rows), 'pilot':sum(bool(r.get('pilot')) for r in records),
            'table':[dict(folder=k[0], status=k[1], evidence_source=k[2], all=counts[k,'all'], pilot=counts[k,'pilot']) for k in keys],
            'records':rows}
