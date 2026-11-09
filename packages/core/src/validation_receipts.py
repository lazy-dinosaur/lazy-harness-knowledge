"""PostgreSQL typed diagnostic persistence; no fallback canonical file log."""
import json

from write_contract import WriteContractError

MIGRATION = '0023_validation_receipt.sql'


class ValidationReceiptMigrationRequired(WriteContractError):
    def __init__(self):
        super().__init__([dict(code='E_MIGRATION_REQUIRED', where='knowledge.validation_receipt',
                               detail='apply ' + MIGRATION + ' before deploying validation failure writes')])


def insert(cur, receipt):
    cur.execute("select to_regclass('knowledge.validation_receipt')")
    if cur.fetchone()[0] is None:
        raise ValidationReceiptMigrationRequired()
    columns = ('receipt_id', 'request_id', 'contract_version', 'profile', 'stage', 'entry_id',
               'host_id', 'work_unit_id', 'judgement_id', 'judgement_version', 'fact_index',
               'payload_digest', 'checked_digest', 'payload', 'checked_payload', 'errors')
    values = [json.dumps(receipt[k], ensure_ascii=False, allow_nan=False)
              if k in ('payload', 'checked_payload', 'errors') else receipt[k] for k in columns]
    placeholders = ['%s::jsonb' if k in ('payload', 'checked_payload', 'errors') else '%s' for k in columns]
    cur.execute('insert into knowledge.validation_receipt (' + ','.join(columns) + ') values (' +
                ','.join(placeholders) + ')', values)
    return receipt['receipt_id']


def save(dsn, receipt):
    import store_pg
    with store_pg.connect(dsn) as conn, conn.cursor() as cur:
        insert(cur, receipt)
    return receipt['receipt_id']


def read(dsn, *, request_id=None, entry_id=None, host=None):
    """Server API; bounded readback. Request/entry selector is mandatory, not authority."""
    if not request_id and not entry_id:
        raise ValueError('request_id or entry_id required')
    import store_pg
    with store_pg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute('''select * from knowledge.validation_receipt
            where (%s::uuid is null or request_id=%s::uuid)
              and (%s::uuid is null or entry_id=%s::uuid)
              and (%s::text is null or host_id=%s) order by created_at,receipt_id limit 200''',
                    (request_id, request_id, entry_id, entry_id, host, host))
        return store_pg._rows(cur)
