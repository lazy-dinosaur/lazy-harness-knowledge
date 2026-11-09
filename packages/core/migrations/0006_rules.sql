-- 0006 rules: rule module storage (separate schema from knowledge; schema-delta '규칙은 소화를 거치지 않고 규칙 도구로').
-- Harness base rules: host_id null, id 'h-N'. Project rules: host_id set, id 'p-N'. Enforcement is derived
-- from level + code_check (no strength column).
create schema if not exists rules;

create sequence rules.rule_seq;

create table rules.rule (
  rule_id text primary key check (rule_id ~ '^(h|p)-[0-9]+$'),
  host_id text references knowledge.host(host_id),
  when_text text not null check (length(btrim(when_text)) > 0),
  must_text text not null check (length(btrim(must_text)) > 0),
  level text not null check (level in ('must', 'should')),
  unless_text text,
  why_text text,
  ref text,
  code_check jsonb check (code_check is null or jsonb_typeof(code_check) = 'object'),
  source jsonb not null check (jsonb_typeof(source) = 'object'),
  status text not null default 'active' check (status in ('active', 'deleted')),
  version integer not null default 1 check (version > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check ((host_id is null) = (rule_id like 'h-%'))
);

create table rules.rule_history (
  history_id bigint generated always as identity primary key,
  rule_id text not null references rules.rule(rule_id),
  version integer not null,
  op text not null check (op in ('create', 'update', 'delete')),
  snapshot jsonb not null,
  source jsonb not null,
  at timestamptz not null default now()
);

create table rules.judgement_receipt (
  receipt_id uuid primary key,
  host_id text not null references knowledge.host(host_id),
  turn_ref text not null,
  rule_id text not null references rules.rule(rule_id),
  rule_version integer not null,
  label text not null check (label in ('not_applicable', 'followed', 'violated', 'unsure')),
  confident boolean not null,
  cond jsonb not null,
  done jsonb not null,
  items jsonb not null default '[]'::jsonb,
  evidence jsonb not null,
  rule_delivered boolean,
  dispute text,
  at timestamptz not null default now()
);

create table rules.injection (
  injection_id bigint generated always as identity primary key,
  host_id text not null references knowledge.host(host_id),
  turn_ref text not null,
  rule_ids text[] not null default '{}',
  knowledge_aliases text[] not null default '{}',
  tokens integer not null default 0 check (tokens >= 0),
  at timestamptz not null default now()
);

create trigger rule_history_append_only before update or delete on rules.rule_history
  for each row execute function knowledge.reject_history_mutation();

alter table rules.rule enable row level security;
alter table rules.rule_history enable row level security;
alter table rules.judgement_receipt enable row level security;
alter table rules.injection enable row level security;
