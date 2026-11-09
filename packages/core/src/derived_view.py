"""Current-row derived reading/embedding view. Canonical fragment text is immutable."""
import fact_text

VARIANT = 'ctx-v2'


def render(domain, subject, parent, text):
    path = ' › '.join(str(x) for x in (domain, parent, subject) if x)
    return (path + ': ' if path else '') + fact_text.view(text)


def fragment(row):
    parent = row.get('parent_subject') or {}
    return render(row.get('domain'), row.get('subject'), parent.get('name') if isinstance(parent, dict) else parent, row['text'])


def identities(cur, ids):
    # G16 merges repoint previous aliases to the survivor (one hop); never use source.subject snapshots.
    cur.execute('''select f.id::text, coalesce(d.merged_into,d.domain,f.domain), s.name, p.name
        from knowledge.fragment f
        left join knowledge.subject old on old.subject_id=f.subject_id
        left join knowledge.subject s on s.subject_id=coalesce(old.merged_into,old.subject_id)
        left join knowledge.subject op on op.subject_id=s.parent_subject_id
        left join knowledge.subject p on p.subject_id=coalesce(op.merged_into,op.subject_id)
        left join knowledge.domain_type d on d.host_id=f.host_id and d.domain=coalesce(s.domain,f.domain)
        where f.id=any(%s::uuid[])''', (list(ids),))
    return {str(fid):(domain, subject, parent) for fid,domain,subject,parent in cur.fetchall()}


def attach(cur, rows):
    values = identities(cur, [str(f['id']) for f in rows]) if rows else {}
    for f in rows:
        if str(f['id']) in values:
            domain, subject, parent = values[str(f['id'])]
            f.update(domain=domain, subject=subject, parent_subject={'name':parent} if parent else None)
            f['view'] = fragment(f)
    return rows
