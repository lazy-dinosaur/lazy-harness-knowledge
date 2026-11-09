-- 0009: subject dictionary (schema-delta '주어는 기록할 때 맞추고 소화할 때 등록' + '도메인도 주어와 같은 분업, 주어 사전은 호스트 전체 하나', 2026-09-30).
-- Local draft only; production application requires separate user approval.
-- One dictionary per host (not per domain). Record time only reads it; digestion (single writer) registers names.
-- fragment.subject_id is nullable: fragments made before this migration keep the old contradiction scan.
begin;

create table knowledge.subject (
  subject_id uuid primary key default gen_random_uuid(),
  host_id text not null references knowledge.host(host_id),
  name text not null check (length(btrim(name)) > 0),
  created_at timestamptz not null default now(),
  unique (host_id, name),
  unique (host_id, subject_id)
);

-- normalised spellings (subject_dict.norm) that mean the subject; the canonical name is one of them
create table knowledge.subject_alias (
  host_id text not null,
  alias text not null check (length(btrim(alias)) > 0),
  subject_id uuid not null,
  created_at timestamptz not null default now(),
  primary key (host_id, alias),
  foreign key (host_id, subject_id) references knowledge.subject(host_id, subject_id)
);

create table knowledge.subject_embedding (
  subject_id uuid not null references knowledge.subject(subject_id),
  model_id text not null,
  embedding extensions.vector not null,
  created_at timestamptz not null default now(),
  primary key (subject_id, model_id)
);

alter table knowledge.fragment add column subject_id uuid references knowledge.subject(subject_id);
create index fragment_host_subject_active_idx on knowledge.fragment(host_id, subject_id) where active;

alter table knowledge.subject enable row level security;
alter table knowledge.subject_alias enable row level security;
alter table knowledge.subject_embedding enable row level security;

commit;
