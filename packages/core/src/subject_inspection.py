"""Bounded subject metadata reads. No identity selection, merge following or writes."""
import base64
import binascii
import json
from uuid import UUID

import subject_dict

MAX_LIMIT = 100
FIELDS = ('host_id', 'domain', 'subject_id', 'name', 'subject_type', 'description',
          'parent_subject_id', 'merged_into', 'domain_status', 'domain_merged_into',
          'description_truncated')
SELECT = """select s.host_id,s.domain,s.subject_id::text,s.name,s.subject_type,
                   left(s.description,300),s.parent_subject_id::text,s.merged_into::text,
                   d.status,d.merged_into,length(s.description)>300
            from knowledge.subject s
            join knowledge.domain_type d on d.host_id=s.host_id and d.domain=s.domain
            where s.host_id=%s and (%s::text is null or s.domain=%s)"""


def _text(value, field, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\x00' in value:
        raise ValueError(field + ' must be a nonempty bounded string')
    return value


def _uuid(value, field):
    _text(value, field, 36)
    try:
        if str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ValueError(field + ' must be a canonical UUID') from None
    return value


def _cursor(host, domain, after):
    raw = json.dumps([1, host, domain, after], ensure_ascii=False, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _after(token, host, domain):
    _text(token, 'cursor', 4096)
    try:
        raw = json.loads(base64.b64decode(token, altchars=b'-_', validate=True))
        if not isinstance(raw, list) or len(raw) != 4 or type(raw[0]) is not int or raw[:3] != [1, host, domain]:
            raise ValueError()
        return _uuid(raw[3], 'cursor')
    except (ValueError, TypeError, UnicodeError, binascii.Error):
        raise ValueError('invalid cursor for current read scope') from None


def _item(row):
    item = dict(zip(FIELDS, row))
    item['status'] = ('merged' if item['merged_into'] else
                      'retired' if item['domain_status'] == 'retired' else 'present')
    return item


def inspect_subjects(connect, runtime_db, host, data):
    """One read core; host/context are server-owned, never selected by public JSON."""
    _text(host, 'configured default_host', 256)
    if not isinstance(data, dict):
        raise ValueError('input must be an object')
    action = data.get('action')
    allowed = {'list': {'action', 'domain', 'limit', 'cursor'},
               'inspect': {'action', 'domain', 'subject_id'},
               'lookup': {'action', 'domain', 'name'}}
    if not isinstance(action, str) or action not in allowed:
        raise ValueError('subject action must be list, inspect or lookup (read-only)')
    if data.keys() - allowed[action]:
        raise ValueError('unsupported subject input fields')
    domain = _text(data['domain'], 'domain', 256) if 'domain' in data else None
    sid, name, after = None, None, None
    limit = data.get('limit', 20)
    if action == 'list':
        if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
            raise ValueError('limit must be an integer from 1 to 100')
        if 'cursor' in data:
            after = _after(data['cursor'], host, domain)
    elif action == 'inspect':
        sid = _uuid(data.get('subject_id'), 'subject_id')
    else:
        if domain is None:
            raise ValueError('lookup requires an exact domain')
        name = _text(data.get('name'), 'name', 300)
        if not subject_dict.norm(name):
            raise ValueError('name must have a nonempty dictionary spelling')
    # Recheck the existing immutable target/ambient-routing contract before connector use.
    dsn = runtime_db.require_same_target(runtime_db.dsn)
    result = {'ok': True, 'action': action, 'host_id': host, 'items': [], 'next_cursor': None}
    with connect(dsn) as conn, conn.cursor() as cur:
        # This is the first statement: even an accidental downstream writer fails in PostgreSQL.
        cur.execute('SET TRANSACTION READ ONLY')
        kind = None
        if action == 'lookup':
            hit = subject_dict.lookup(cur, host, name, domain, exact_only=True)
            if hit:
                sid, kind = hit['subject_id'], hit['kind']
            else:
                # Legacy/imported subjects may predate their spelling alias row.
                cur.execute(SELECT + ' and s.name=%s order by s.subject_id limit 1', (host, domain, domain, name))
                row = cur.fetchone()
                if row:
                    result['items'] = [_item(row)]
                    kind = 'name'
                result.update(status='found' if row else 'not_found', match_kind=kind)
                return result
        if action == 'list':
            cur.execute(SELECT + ' and (%s::uuid is null or s.subject_id>%s::uuid) order by s.subject_id limit %s',
                        (host, domain, domain, after, after, limit + 1))
            rows = cur.fetchall()
            result['items'] = [_item(row) for row in rows[:limit]]
            if len(rows) > limit:
                result['next_cursor'] = _cursor(host, domain, result['items'][-1]['subject_id'])
            result['status'] = 'listed'
        else:
            # No global-ID existence check: foreign and missing IDs have identical responses.
            cur.execute(SELECT + ' and s.subject_id=%s::uuid limit 1', (host, domain, domain, sid))
            row = cur.fetchone()
            result['items'] = [_item(row)] if row else []
            result['status'] = 'found' if row else 'not_found'
            if action == 'lookup':
                result['match_kind'] = kind if row else None
    return result
