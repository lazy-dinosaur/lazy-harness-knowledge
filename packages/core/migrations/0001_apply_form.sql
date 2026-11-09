-- Local test approved; production deployment requires separate approval.

create schema if not exists knowledge;
-- Supabase supplies the extensions schema; neither extension is used by a table yet.
create extension if not exists vector with schema extensions;
create extension if not exists pg_trgm with schema extensions;

create type knowledge.fragment_kind as enum ('fact','decision','rationale','rejected','constraint','procedure','term','question');
create type knowledge.fragment_confidence as enum ('confirmed','candidate','contested');
create type knowledge.relation_type as enum ('supersedes','conflicts-with');
create type knowledge.work_unit_status as enum ('active','completed','abandoned');
create type knowledge.ledger_state as enum ('proposed','rejected_input','review_queue','provisional','closed','eligible','absorbed','retained_as_evidence','expired');
create type knowledge.receipt_stage as enum ('worktime','digestion');
create type knowledge.receipt_combined as enum ('record','no_record','duplicate_skip','needs_review','update_record','deprecate_record');
create type knowledge.absorption_decision as enum ('absorbed','retained_as_evidence','rejected');
create type knowledge.absorption_decider as enum ('acceptance_policy','human');
create type knowledge.policy_action as enum ('absorb','retain_as_evidence','reject','ask_now','queue_for_human','hold');
create type knowledge.confirmation_status as enum ('pending','answered','expired');
create type knowledge.template_status as enum ('active','retired');

-- A reference is {type, locator, quote}; extra metadata is allowed, required keys are not.
create function knowledge.valid_evidence_refs(refs jsonb) returns boolean
language sql immutable set search_path = '' as $$
  select coalesce(jsonb_typeof(refs) = 'array' and not exists (
    select 1 from jsonb_array_elements(case when jsonb_typeof(refs) = 'array' then refs else '[]'::jsonb end) as r(value)
    where jsonb_typeof(r.value) is distinct from 'object'
       or r.value->>'type' is null
       or r.value->>'type' not in ('code_test','official_doc','user_utterance','observed_output','ai_inference')
       or jsonb_typeof(r.value->'locator') is distinct from 'string'
       or nullif(r.value->>'locator','') is null
       or jsonb_typeof(r.value->'quote') is distinct from 'string'
  ), false);
$$;

create function knowledge.valid_judgement_evidence(body jsonb) returns boolean
language sql immutable set search_path = '' as $$
  select coalesce(jsonb_typeof(body->'facts') = 'array' and
    knowledge.valid_evidence_refs(coalesce(body->'evidence_refs','[]'::jsonb)) and
    not exists (
      select 1 from jsonb_array_elements(case when jsonb_typeof(body->'facts') = 'array' then body->'facts' else '[]'::jsonb end) as f(value)
      where jsonb_typeof(f.value) is distinct from 'object'
         or not knowledge.valid_evidence_refs(coalesce(f.value->'evidence_refs','[]'::jsonb))
    ), false);
$$;

create function knowledge.reject_history_mutation() returns trigger
language plpgsql set search_path = '' as $$
begin
  raise exception '% is append-only', tg_table_name;
end;
$$;

create table knowledge.host (
  host_id text primary key,
  name text not null,
  repo_locator text not null,
  cross_search_allowed boolean not null default false,
  created_at timestamptz not null default now()
);

-- Only canonical fragments are stored here: no workspace_id.
create table knowledge.fragment (
  id uuid primary key default gen_random_uuid(),
  host_id text not null references knowledge.host(host_id),
  alias text not null,
  domain text not null,
  seq integer not null check (seq > 0),
  text text not null,
  keywords text[] not null default '{}',
  kind knowledge.fragment_kind not null,
  group_id text,
  revision integer not null default 1 check (revision > 0),
  active boolean not null default true,
  confidence knowledge.fragment_confidence not null default 'confirmed',
  source jsonb not null check (jsonb_typeof(source) = 'object' and knowledge.valid_evidence_refs(source->'evidence_refs')),
  valid_from timestamptz,
  superseded_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (host_id, domain, seq),
  unique (host_id, id)
);
create index fragment_host_domain_active_idx on knowledge.fragment(host_id, domain) where active;
create index fragment_host_kind_active_idx on knowledge.fragment(host_id, kind) where active;
create index fragment_group_idx on knowledge.fragment(group_id);
create index fragment_keywords_idx on knowledge.fragment using gin(keywords);
create index fragment_text_trgm_idx on knowledge.fragment using gin(text extensions.gin_trgm_ops);

create table knowledge.fragment_history (
  history_id bigint generated always as identity primary key,
  fragment_id uuid not null references knowledge.fragment(id),
  revision integer not null check (revision > 0),
  snapshot jsonb not null check (jsonb_typeof(snapshot) = 'object'),
  changed_at timestamptz not null default now(),
  actor text,
  absorption_id uuid,
  op text not null check (op in ('create','update','deprecate')),
  unique (fragment_id, revision)
);
create trigger fragment_history_append_only before update or delete on knowledge.fragment_history
for each row execute function knowledge.reject_history_mutation();

-- The trigger owns fragment revision history, including the creation snapshot.
-- Empty transaction-local settings are treated as absent. Invalid nonempty UUIDs fail.
create function knowledge.track_fragment_change() returns trigger
language plpgsql set search_path = '' as $$
declare
  absorption_setting text := nullif(current_setting('knowledge.absorption_id', true), '');
  history_actor text := nullif(current_setting('knowledge.actor', true), '');
begin
  if tg_op = 'DELETE' then
    raise exception 'fragment DELETE is forbidden; deprecate with active=false UPDATE';
  elsif tg_op = 'INSERT' then
    insert into knowledge.fragment_history(fragment_id, revision, snapshot, actor, absorption_id, op)
    values (new.id, new.revision, to_jsonb(new), history_actor, absorption_setting::uuid, 'create');
  else
    if new.revision <> old.revision + 1 then
      raise exception 'fragment revision must advance by exactly one';
    end if;
    insert into knowledge.fragment_history(fragment_id, revision, snapshot, actor, absorption_id, op)
    values (old.id, new.revision, to_jsonb(old), history_actor, absorption_setting::uuid,
            case when old.active and not new.active then 'deprecate' else 'update' end);
    new.updated_at := now();
  end if;
  return new;
end;
$$;
create trigger fragment_track_insert after insert on knowledge.fragment
for each row execute function knowledge.track_fragment_change();
create trigger fragment_track_update before update on knowledge.fragment
for each row execute function knowledge.track_fragment_change();
create trigger fragment_forbid_delete before delete on knowledge.fragment
for each row execute function knowledge.track_fragment_change();

-- Two composite FKs enforce the shared host without trusting application code.
create table knowledge.relation (
  host_id text not null references knowledge.host(host_id),
  src uuid not null,
  dst uuid not null,
  type knowledge.relation_type not null,
  why text not null,
  confidence knowledge.fragment_confidence not null default 'candidate',
  created_at timestamptz not null default now(),
  primary key (src, dst, type),
  foreign key (host_id, src) references knowledge.fragment(host_id, id),
  foreign key (host_id, dst) references knowledge.fragment(host_id, id),
  check (src <> dst)
);

create table knowledge.work_unit (
  work_unit_id uuid primary key,
  host_id text not null references knowledge.host(host_id),
  partition_key text not null,
  status knowledge.work_unit_status not null default 'active',
  completion_sources jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  unique (work_unit_id, host_id, partition_key)
);
create table knowledge.work_unit_event (
  event_id bigint generated always as identity primary key,
  work_unit_id uuid not null references knowledge.work_unit(work_unit_id),
  "from" knowledge.work_unit_status,
  "to" knowledge.work_unit_status not null,
  source text not null,
  "at" timestamptz not null default now()
);
create trigger work_unit_event_append_only before update or delete on knowledge.work_unit_event
for each row execute function knowledge.reject_history_mutation();

create table knowledge.ledger_entry (
  entry_id uuid primary key,
  host_id text not null,
  partition_key text not null,
  work_unit_id uuid not null,
  judgement_id text not null,
  judgement_version integer not null check (judgement_version > 0),
  judgement_body jsonb not null check (knowledge.valid_judgement_evidence(judgement_body)),
  state knowledge.ledger_state not null default 'proposed',
  supplement_count integer not null default 0 check (supplement_count between 0 and 2),
  created_at timestamptz not null default now(),
  unique (judgement_id, judgement_version),
  unique (entry_id, work_unit_id),
  foreign key (work_unit_id, host_id, partition_key) references knowledge.work_unit(work_unit_id, host_id, partition_key)
);
create index ledger_partition_state_idx on knowledge.ledger_entry(partition_key, state);
create table knowledge.ledger_entry_event (
  event_id bigint generated always as identity primary key,
  entry_id uuid not null references knowledge.ledger_entry(entry_id),
  "from" knowledge.ledger_state,
  "to" knowledge.ledger_state not null,
  actor text not null check (actor in ('worker','luna','runner','jev','digester','acceptance','human')),
  receipt_ref uuid,
  "at" timestamptz not null default now()
);
create trigger ledger_entry_event_append_only before update or delete on knowledge.ledger_entry_event
for each row execute function knowledge.reject_history_mutation();

create table knowledge.question_template (
  template_id text not null,
  template_version text not null,
  questions jsonb not null check (jsonb_typeof(questions) = 'object'),
  status knowledge.template_status not null default 'active',
  created_at timestamptz not null default now(),
  primary key (template_id, template_version)
);
create table knowledge.check_receipt (
  receipt_id uuid primary key,
  entry_id uuid not null references knowledge.ledger_entry(entry_id),
  fact_index integer not null check (fact_index >= 0),
  stage knowledge.receipt_stage not null,
  template_id text not null,
  template_version text not null,
  jev_model_requested text not null,
  jev_model_actual text not null,
  dedup_key text not null unique,
  packet jsonb not null check (jsonb_typeof(packet) = 'object' and
    knowledge.valid_evidence_refs(coalesce(packet->'evidence_refs','[]'::jsonb))),
  answers jsonb not null,
  combined knowledge.receipt_combined not null,
  review_reasons jsonb not null,
  target_fragment_id uuid references knowledge.fragment(id),
  target_revision_seen integer check (target_revision_seen > 0),
  created_at timestamptz not null default now(),
  foreign key (template_id, template_version) references knowledge.question_template(template_id, template_version),
  check ((target_fragment_id is null) = (target_revision_seen is null))
);

create table knowledge.acceptance_policy (
  policy_id bigint generated always as identity primary key,
  host_id text references knowledge.host(host_id),
  version integer not null check (version > 0),
  ordinal integer not null check (ordinal > 0),
  rule_id text not null,
  when_json jsonb not null check (jsonb_typeof(when_json) = 'object'),
  then_action knowledge.policy_action not null,
  unique nulls not distinct (host_id, version, ordinal)
);

create table knowledge.absorption (
  absorption_id uuid primary key,
  work_unit_id uuid not null references knowledge.work_unit(work_unit_id),
  entry_id uuid not null,
  fact_index integer not null check (fact_index >= 0),
  digestion_receipt_id uuid not null references knowledge.check_receipt(receipt_id),
  proposal jsonb not null,
  decision knowledge.absorption_decision not null,
  decided_by knowledge.absorption_decider not null,
  rule_id text,
  action knowledge.policy_action,
  fragment_ref uuid references knowledge.fragment(id),
  fragment_revision_after integer check (fragment_revision_after > 0),
  created_at timestamptz not null default now(),
  foreign key (entry_id, work_unit_id) references knowledge.ledger_entry(entry_id, work_unit_id),
  check ((fragment_ref is null) = (fragment_revision_after is null))
);
alter table knowledge.fragment_history add constraint fragment_history_absorption_fk
  foreign key (absorption_id) references knowledge.absorption(absorption_id) deferrable initially deferred;
alter table knowledge.ledger_entry_event add constraint ledger_event_receipt_fk
  foreign key (receipt_ref) references knowledge.check_receipt(receipt_id) deferrable initially deferred;

create table knowledge.confirmation_queue (
  confirmation_id bigint generated always as identity primary key,
  entry_id uuid not null references knowledge.ledger_entry(entry_id),
  fact_index integer not null check (fact_index >= 0),
  rule_id text not null,
  reason text not null,
  status knowledge.confirmation_status not null default 'pending',
  question_text text,
  answer text,
  created_at timestamptz not null default now(),
  answered_at timestamptz
);

-- policy.py DEFAULT_POLICY: first match wins; no match = hold.
insert into knowledge.acceptance_policy(host_id, version, ordinal, rule_id, when_json, then_action) values
(null,1,1,'P01','{"completion":false}','hold'),
(null,1,2,'P02','{"conflict":true}','queue_for_human'),
(null,1,3,'P03','{"combined":"duplicate_skip"}','reject'),
(null,1,4,'P04','{"combined":"no_record"}','retain_as_evidence'),
(null,1,5,'P05','{"combined":"needs_review","review_reasons":["impact: reference_only (policy pending)"]}','retain_as_evidence'),
(null,1,6,'P06','{"combined":"needs_review","can_ask_now":true}','ask_now'),
(null,1,7,'P07','{"combined":"needs_review"}','queue_for_human'),
(null,1,8,'P08','{"kind":["decision","constraint"],"evidence_source":["user_tentative","ai_inference","observed_output"],"can_ask_now":true}','ask_now'),
(null,1,9,'P09','{"kind":["decision","constraint"],"evidence_source":["user_tentative","ai_inference","observed_output"]}','queue_for_human'),
(null,1,10,'P10','{"combined":["record","update_record","deprecate_record"]}','absorb');

-- is_new/is_supported/durability: experiments/v2-knowledge-module-runner-01/pilot-d/packet-A0.json
-- impact: lazy-harness repository: .lazy-harness/evidence/jev-impact-stability-14-plan.json (question.impact)
insert into knowledge.question_template(template_id, template_version, questions) values
('record-need','v0.2.2',$json${
 "is_new": {
  "type": "noul",
  "instructions": "candidate_fact 가 existing_records_excerpt 에 제시된 기존 기록에 없는 새 내용인가?",
  "criteria": {
   "true": "새 내용이다",
   "false": "새 내용이 아니다"
  }
 },
 "is_supported": {
  "type": "choice",
  "instructions": "evidence_quote 가 candidate_fact 를 직접 지지하는가?",
  "criteria": {
   "supported": "직접 지지한다",
   "contradicted": "직접 모순된다",
   "insufficient": "직접 지지하거나 모순한다고 판단하기에 부족하다",
   "none_or_uncertain": "판단할 수 없거나 불확실하다"
  }
 },
 "durability": {
  "type": "choice",
  "instructions": "candidate_fact 는 이후 작업에서도 유효한 사실·결정·제약·정의/스펙(공식·키·스키마 포함)인가, 아니면 일시적 진행 정보나 설명에 그치는가?",
  "criteria": {
   "durable_fact": "이후 작업에서도 유효한 사실·결정·제약·정의/스펙(공식·키·스키마 포함)이다.",
   "transient_progress": "일시적 진행 정보다.",
   "explanation_only": "설명에 그친다.",
   "none_or_uncertain": "판단할 수 없거나 불확실하다."
  }
 },
 "impact": {
  "type": "choice",
  "instructions": "candidate_fact 는 이후 작업의 결정이나 행동에 어떤 영향을 주는가?",
  "criteria": {
   "changes_decisions": "이후 작업의 결정·설계·구현 방식을 바꾸거나 제약한다",
   "reference_only": "참고는 되지만 결정이나 행동을 바꾸지 않는다",
   "no_future_use": "이후 작업에 쓰이지 않는다",
   "none_or_uncertain": "판단할 수 없거나 불확실하다"
  }
 }
}$json$::jsonb),
-- update wire: experiments/v2-knowledge-module-runner-01/pilot-d/packet-A1.json
('intent','v0.3.1',$json${
 "differs_from_target": {
  "type": "noul",
  "instructions": "candidate_fact 는 target_excerpt 의 내용과 실질적으로 다른 주장인가?",
  "criteria": {
   "true": "실질적으로 다른 주장이다",
   "false": "실질적으로 다르지 않다"
  }
 },
 "correction_evidence": {
  "type": "choice",
  "instructions": "evidence_quote 가 target_excerpt 의 내용이 더 이상 정확하지 않음을 직접 보여주는가?",
  "criteria": {
   "refutes": "target_excerpt 의 내용을 반증한다",
   "consistent": "target_excerpt 의 내용과 양립하며 이를 반증하지 않는다",
   "insufficient": "판정하기에 부족하다",
   "none_or_uncertain": "판단할 수 없거나 불확실하다"
  }
 },
 "durability": {
  "type": "choice",
  "instructions": "candidate_fact 는 이후 작업에서도 유효한 사실·결정·제약·정의/스펙(공식·키·스키마 포함)인가, 아니면 일시적 진행 정보나 설명에 그치는가?",
  "criteria": {
   "durable_fact": "이후 작업에서도 유효한 사실·결정·제약·정의/스펙(공식·키·스키마 포함)이다.",
   "transient_progress": "일시적 진행 정보다.",
   "explanation_only": "설명에 그친다.",
   "none_or_uncertain": "판단할 수 없거나 불확실하다."
  }
 },
 "scope": {
  "type": "choice",
  "instructions": "candidate_fact 의 범위는 evidence_quote 가 정당화하는 범위와 비교해 어떠한가?",
  "criteria": {
   "preserved": "정당화되는 범위를 보존한다",
   "expanded": "정당화되는 범위보다 넓다",
   "narrowed": "정당화되는 범위보다 좁다",
   "none_or_uncertain": "판단할 수 없거나 불확실하다"
  }
 }
}$json$::jsonb),
-- lazy-harness repository: .lazy-harness/evidence/jev-utterance-status-15-plan.json (question)
('utterance_status','v1',$json${
 "utterance_status": {
  "type": "choice",
  "instructions": "user_utterance 는 결정을 확정한 발언인가, 아직 검토 중인 의견·질문인가?",
  "criteria": {
   "confirmed_decision": "선택·지시·정정으로 결정을 확정한 발언이다",
   "tentative_opinion": "의견·추측·질문이며 결정을 확정하지 않은 발언이다",
   "none_or_uncertain": "판단할 수 없거나 불확실하다"
  }
 }
}$json$::jsonb);

alter table knowledge.host enable row level security;
alter table knowledge.fragment enable row level security;
alter table knowledge.fragment_history enable row level security;
alter table knowledge.relation enable row level security;
alter table knowledge.work_unit enable row level security;
alter table knowledge.work_unit_event enable row level security;
alter table knowledge.ledger_entry enable row level security;
alter table knowledge.ledger_entry_event enable row level security;
alter table knowledge.check_receipt enable row level security;
alter table knowledge.absorption enable row level security;
alter table knowledge.acceptance_policy enable row level security;
alter table knowledge.confirmation_queue enable row level security;
alter table knowledge.question_template enable row level security;
revoke all on schema knowledge from anon, authenticated, public;
revoke all on all tables in schema knowledge from anon, authenticated, public;
revoke all on all sequences in schema knowledge from anon, authenticated, public;
revoke all on all functions in schema knowledge from anon, authenticated, public;
-- Objects created later in this schema must not inherit public API access.
alter default privileges in schema knowledge revoke all on tables from anon, authenticated, public;
alter default privileges in schema knowledge revoke all on sequences from anon, authenticated, public;
alter default privileges in schema knowledge revoke all on functions from anon, authenticated, public;
