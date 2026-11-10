"""Server-owned selected identity. No name, vector, or semantic routing here."""
from uuid import UUID


class IdentityError(ValueError):
    pass


def checked(ref, host, domain):
    if not isinstance(ref, dict) or set(ref) != {'host_id', 'domain', 'subject_id'}:
        raise IdentityError('E_IDENTITY: host/domain/subject_id ref required')
    if ref['host_id'] != host or ref['domain'] != domain or not host or not domain:
        raise IdentityError('E_IDENTITY: cross-host/domain ref')
    try:
        sid = str(UUID(ref['subject_id']))
    except (ValueError, TypeError, AttributeError) as exc:
        raise IdentityError('E_IDENTITY: invalid subject UUID') from exc
    return dict(host_id=host, domain=domain, subject_id=sid)


def resolve(cur, ref, host, domain):
    """Follow only recorded merged_into; validate every hop inside the selected boundary.

    Subjects have no active column: merged_into IS NULL is the surviving subject.
    The owning domain must be active and not merged. Domain moves need re-selection,
    not a guessed equivalence between composite keys.
    """
    ref = checked(ref, host, domain)
    cur.execute('select status,merged_into from knowledge.domain_type where host_id=%s and domain=%s for share',
                (host, domain))
    area = cur.fetchone()
    if not area or area[0] != 'active' or area[1] is not None:
        raise IdentityError('E_IDENTITY: inactive/merged/missing domain')
    sid, seen = ref['subject_id'], set()
    while sid not in seen:
        seen.add(sid)
        cur.execute('''select subject_id::text,name,domain,merged_into::text,subject_type,description
            from knowledge.subject where host_id=%s and subject_id=%s for share''', (host, sid))
        row = cur.fetchone()
        if not row or row[2] != domain:
            raise IdentityError('E_IDENTITY: subject outside selected host/domain')
        if row[3] is None:
            return dict(subject_id=row[0], name=row[1], domain=row[2], subject_type=row[4],
                        description=row[5], parent_subject=None)
        sid = row[3]
    raise IdentityError('E_IDENTITY: subject merge cycle')


def materialize(cur, ref, host, domain, name, description, subject_type=None):
    """Explicit-ID branch of subject_dict.register; name conflicts never pick an ID.

    UUIDs use the existing UUID column (DB default remains the legacy authority).
    Selection may allocate a UUID before the row exists, then this transaction
    preserves that exact UUID. No schema/placeholder type is needed.
    """
    import subject_dict as sd
    ref = checked(ref, host, domain)
    sd._lock_name(cur, host, name, domain)
    cur.execute('select host_id,domain,name from knowledge.subject where subject_id=%s', (ref['subject_id'],))
    row = cur.fetchone()
    if row:
        if row != (host, domain, name):
            raise IdentityError('E_IDENTITY: allocated UUID already owned')
        return resolve(cur, ref, host, domain)['subject_id']
    cur.execute('select status,merged_into from knowledge.domain_type where host_id=%s and domain=%s for share', (host, domain))
    area = cur.fetchone()
    if not area or area[0] != 'active' or area[1] is not None:
        raise IdentityError('E_IDENTITY: cannot install in inactive domain')
    # register already checked metadata. DO NOTHING is intentional: a conflicting
    # canonical name must never silently substitute the other row's UUID.
    cur.execute('''insert into knowledge.subject(subject_id,host_id,domain,name,subject_type,description)
        values(%s,%s,%s,%s,%s,%s) on conflict do nothing returning subject_id::text''',
                (ref['subject_id'], host, domain, name, subject_type, description.strip()))
    inserted = cur.fetchone()
    if not inserted or inserted[0] != ref['subject_id']:
        raise IdentityError('E_IDENTITY: UUID/name insertion conflict')
    add_spelling(cur, ref, name)
    return ref['subject_id']


def add_spelling(cur, ref, alias):
    """Retain the existing spelling/core policy, but fail on any foreign owner.

    These lookups are collision checks, never entity selection. No alias can
    change the selected UUID, including a race at insertion.
    """
    import subject_dict as sd
    ref = checked(ref, ref['host_id'], ref['domain'])
    sd._lock_name(cur, ref['host_id'], alias, ref['domain'])
    for key in dict.fromkeys((alias, sd.norm(alias), sd.core(alias))):
        cur.execute('''insert into knowledge.subject_alias(host_id,domain,alias,subject_id)
            values(%s,%s,%s,%s) on conflict(host_id,domain,alias) do update
            set subject_id=knowledge.subject_alias.subject_id
            where knowledge.subject_alias.subject_id=excluded.subject_id returning subject_id::text''',
                    (ref['host_id'], ref['domain'], key, ref['subject_id']))
        row = cur.fetchone()
        if not row or row[0] != ref['subject_id']:
            raise IdentityError('E_IDENTITY: alias owned by another subject')
