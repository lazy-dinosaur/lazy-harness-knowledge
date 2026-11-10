-- Source-proven citation-only repair. Claim fields remain byte-for-byte unchanged.
begin;
create table knowledge.source_metadata_repair_receipt (
  repair_id uuid primary key default gen_random_uuid(),
  host_id text not null references knowledge.host(host_id),
  fragment_id uuid not null,
  old_revision integer not null check (old_revision > 0),
  new_revision integer not null check (new_revision = old_revision + 1),
  claim_identity_hash text not null check (claim_identity_hash ~ '^[0-9a-f]{64}$'),
  old_source_hash text not null check (old_source_hash ~ '^[0-9a-f]{64}$'),
  new_refs_digest text not null check (new_refs_digest ~ '^[0-9a-f]{64}$'),
  source_root_hash text not null check (source_root_hash ~ '^[0-9a-f]{64}$'),
  source_origin text not null check (source_origin in ('live-root','frozen-import')),
  actor text not null check (actor = 'source-metadata-repair'),
  repair_key text not null check (repair_key ~ '^[0-9a-f]{64}$'),
  evidence jsonb not null check (jsonb_typeof(evidence) = 'object'),
  created_at timestamptz not null default now(),
  foreign key (host_id, fragment_id) references knowledge.fragment(host_id,id),
  unique (host_id, repair_key)
);
create trigger source_metadata_repair_receipt_append_only before update or delete on knowledge.source_metadata_repair_receipt
for each row execute function knowledge.reject_history_mutation();
alter table knowledge.source_metadata_repair_receipt enable row level security;
revoke all on knowledge.source_metadata_repair_receipt from anon, authenticated, public;
commit;
