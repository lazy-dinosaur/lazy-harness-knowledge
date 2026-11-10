-- Additive only. Authored offline; MUST be explicitly applied before failure-writing code is deployed.
-- A rejected request may have no valid host/work-unit/ledger row; attempted context is
-- diagnostic data, not a grant. check_receipt remains reserved for actual model checks.
begin;
create table knowledge.validation_receipt (
  receipt_id uuid primary key,
  request_id uuid not null,
  contract_version text not null,
  profile text not null check (profile in ('public-record/v1','pg-ledger/v1','file-ledger/v1')),
  stage text not null check (stage in ('record','register','worktime','worktime_prepare')),
  entry_id uuid references knowledge.ledger_entry(entry_id),
  host_id text,
  work_unit_id text,
  judgement_id text,
  judgement_version integer check (judgement_version > 0),
  fact_index integer check (fact_index >= 0),
  payload_digest text not null check (payload_digest ~ '^[0-9a-f]{64}$'),
  checked_digest text not null check (checked_digest ~ '^[0-9a-f]{64}$'),
  payload jsonb not null,
  checked_payload jsonb not null,
  errors jsonb not null check (jsonb_typeof(errors) = 'array' and jsonb_array_length(errors) > 0),
  created_at timestamptz not null default now()
);
create index validation_receipt_entry_idx on knowledge.validation_receipt(entry_id,created_at);
create index validation_receipt_request_idx on knowledge.validation_receipt(request_id);
create trigger validation_receipt_append_only before update or delete on knowledge.validation_receipt
for each row execute function knowledge.reject_history_mutation();
alter table knowledge.validation_receipt enable row level security;
revoke all on knowledge.validation_receipt from anon, authenticated, public;
-- Separate FK: never point the existing model-check receipt_ref at diagnostics.
alter table knowledge.ledger_entry_event add column validation_receipt_ref uuid
  references knowledge.validation_receipt(receipt_id);
commit;
