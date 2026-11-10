-- Source-origin qualification for parent-directed exception/alias hints; never grants merge authority.
begin;
create table knowledge.source_hint_receipt (
  hint_receipt_id uuid primary key default gen_random_uuid(),
  host_id text not null references knowledge.host(host_id),
  confirmation_id bigint not null references knowledge.confirmation_queue(confirmation_id),
  rule_id text not null check (rule_id in ('canon_exception','alias_conflict')),
  outcome text not null check (outcome in ('complementary','different_target','historical_or_scope','distinct_subject','same_subject_merge_required')),
  reason_digest text not null check (reason_digest ~ '^[0-9a-f]{64}$'),
  source_receipt_key text not null check (source_receipt_key ~ '^[0-9a-f]{64}$'),
  evidence jsonb not null check (jsonb_typeof(evidence)='object'),
  created_at timestamptz not null default now(),
  unique (host_id,confirmation_id,source_receipt_key,outcome),
  check ((rule_id='canon_exception' and outcome in ('complementary','different_target','historical_or_scope')) or
         (rule_id='alias_conflict' and outcome in ('distinct_subject','same_subject_merge_required')))
);
create trigger source_hint_receipt_append_only before update or delete on knowledge.source_hint_receipt
for each row execute function knowledge.reject_history_mutation();
alter table knowledge.source_hint_receipt enable row level security;
revoke all on knowledge.source_hint_receipt from anon,authenticated,public;
commit;
