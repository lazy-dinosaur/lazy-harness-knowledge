-- Separate source-backed canonical correction writer; source-review receipts remain non-mutating.
begin;
create table knowledge.source_correction_receipt (
  correction_id uuid primary key default gen_random_uuid(),
  host_id text not null references knowledge.host(host_id),
  fragment_id uuid not null,
  old_revision integer not null check (old_revision > 0),
  new_revision integer not null check (new_revision = old_revision + 1),
  old_text text not null,
  new_text text not null,
  source_fingerprint text not null check (source_fingerprint ~ '^[0-9a-f]{64}$'),
  source_refs_digest text not null check (source_refs_digest ~ '^[0-9a-f]{64}$'),
  origin text not null check (origin in ('live-root','frozen-import','verbatim-utterance','mixed')),
  correction_key text not null check (correction_key ~ '^[0-9a-f]{64}$'),
  evidence jsonb not null check (jsonb_typeof(evidence) = 'object'),
  created_at timestamptz not null default now(),
  foreign key (host_id, fragment_id) references knowledge.fragment(host_id,id),
  unique (host_id, correction_key),
  check (old_text <> new_text)
);
create trigger source_correction_receipt_append_only before update or delete on knowledge.source_correction_receipt
for each row execute function knowledge.reject_history_mutation();
alter table knowledge.source_correction_receipt enable row level security;
revoke all on knowledge.source_correction_receipt from anon, authenticated, public;
commit;
