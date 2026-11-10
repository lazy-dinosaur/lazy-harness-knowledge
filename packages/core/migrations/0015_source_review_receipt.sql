-- Append-only source-origin semantic review evidence, distinct from human review_resolve answers.
begin;

create table knowledge.source_review_receipt (
  receipt_id uuid primary key default gen_random_uuid(),
  host_id text not null references knowledge.host(host_id),
  left_fragment_id uuid not null,
  left_revision integer not null check (left_revision > 0),
  right_fragment_id uuid not null,
  right_revision integer not null check (right_revision > 0),
  origin text not null check (origin in ('live-root', 'frozen-import', 'verbatim-utterance', 'mixed')),
  left_source_fingerprint text not null check (left_source_fingerprint ~ '^[0-9a-f]{64}$'),
  right_source_fingerprint text not null check (right_source_fingerprint ~ '^[0-9a-f]{64}$'),
  left_source_refs_digest text not null check (left_source_refs_digest ~ '^[0-9a-f]{64}$'),
  right_source_refs_digest text not null check (right_source_refs_digest ~ '^[0-9a-f]{64}$'),
  verdict text not null check (verdict in ('compatible', 'different_target', 'historical_or_scope', 'genuine_conflict')),
  confidence double precision not null check (confidence >= 0.8 and confidence <= 1),
  receipt_key text not null check (receipt_key ~ '^[0-9a-f]{64}$'),
  evidence jsonb not null check (jsonb_typeof(evidence) = 'object'),
  created_at timestamptz not null default now(),
  foreign key (host_id, left_fragment_id) references knowledge.fragment(host_id, id),
  foreign key (host_id, right_fragment_id) references knowledge.fragment(host_id, id),
  check (left_fragment_id <> right_fragment_id),
  unique (host_id, receipt_key)
);

create index source_review_receipt_pair_idx on knowledge.source_review_receipt
  (host_id, left_fragment_id, left_revision, right_fragment_id, right_revision);
create trigger source_review_receipt_append_only before update or delete on knowledge.source_review_receipt
for each row execute function knowledge.reject_history_mutation();
alter table knowledge.source_review_receipt enable row level security;
revoke all on knowledge.source_review_receipt from anon, authenticated, public;

commit;
