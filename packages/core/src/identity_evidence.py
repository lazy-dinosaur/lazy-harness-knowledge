"""Current identity/provenance candidate DATA; never a placement decision.

Uses caller's existing SearchProvider results plus exact current provenance joins.
No ranking, semantic search algorithm, expected UUID, alias or domain assignment.
"""
import copy
import re
from writer_worker import digest
from source_anchor_proof import source_sha


def bundle(unit, source, source_path, subjects, fragments, ranked_ids=()):
    flat = [row for rows in subjects.values() for row in rows]
    by_id = {row['subject_id']: row for row in flat}
    if len(by_id) != len(flat):raise ValueError('current subject catalog duplicate')
    candidates = []
    for f in fragments:
        if not f.get('active') or f.get('host_id') != unit['host_id']:continue
        subject = by_id.get(f.get('subject_id'))
        if subject is None or subject.get('domain') != f['domain']:raise ValueError('current identity provenance join invalid')
        ref = subject.get('identity_ref')
        if ref != {'host_id':unit['host_id'], 'domain':f['domain'], 'subject_id':f['subject_id']}:
            raise ValueError('current identity_ref host/domain mismatch')
        proofs = []
        for v in f.get('source', {}).get('evidence_refs', []):
            match = re.fullmatch(re.escape(source_path) + r'#L([0-9]+)-L([0-9]+)@sha256:([a-f0-9]{64})', v.get('locator', ''))
            if match is None:continue
            a, z = map(int, match.group(1, 2))
            quote = '\n'.join(source.splitlines()[a-1:z])
            if not 1 <= a <= z <= len(source.splitlines()) or match[3] != source_sha(source) or quote != v.get('quote'):
                raise ValueError('current SOURCE hash/ref/quote metadata drift')
            proofs.append(copy.deepcopy(v))
        # Same-document is only a provenance candidate, NOT same-object proof.
        if proofs:
            candidates.append({'subject':copy.deepcopy(subject),
                               'canonical':{k:copy.deepcopy(f[k]) for k in ('id','host_id','domain','subject_id','revision','text','kind','source')},
                               'current_SOURCE_proofs':proofs, 'retrieved_by_existing_provider':f['id'] in ranked_ids,
                               'same_document_is_not_same_object':True, 'not_admission':True})
    candidates.sort(key=lambda v:(v['subject']['domain'], v['subject']['subject_id'], v['canonical']['id']))
    return {'format':'current-identity-source-candidates/1','host_id':unit['host_id'],
            'source_sha256':source_sha(source),'source_path':source_path,
            'current_subject_catalog':copy.deepcopy(flat),'catalog_binding':digest(flat),
            'existing_provider_topK_ids':list(ranked_ids),'candidates':candidates,
            'meaning':'Identity metadata and SOURCE proofs are independent DATA. Same document, literal or association alone does not prove identical object or rule responsibility. Preservation is not prohibition coverage.'}


def collect(policy, unit, source, catalog):
    callback = getattr(policy, 'identity_evidence', None)
    if callback is None:return None
    if not callable(callback):raise ValueError('HOST current identity evidence provider required')
    value = callback(copy.deepcopy(unit), source, copy.deepcopy(catalog))
    if (not isinstance(value, dict) or value.get('format') != 'current-identity-source-candidates/1'
            or value.get('host_id') != unit['host_id'] or value.get('source_sha256') != source_sha(source)
            or not value.get('candidates') or value.get('catalog_binding') != digest(value.get('current_subject_catalog'))):
        raise ValueError('material verified current candidate evidence required')
    return copy.deepcopy(value)
