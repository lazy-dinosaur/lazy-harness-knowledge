-- 0014: merging two existing subjects after the user says they are the same thing (2026-10-03, user 'a', stage 2 of
-- resolving aliases in the subject dictionary; design .local/merge-design.md + astra design review fixes).
-- Option A (user): fragment.subject_id and fragment_leaf.subject_id change in place, no revision bump (the text is
-- unchanged, so [alias@revision] tokens and declared-alias evidence stay valid). Every moved row is logged with its old
-- value; the fragment trigger allows that change only for a row logged by a merge running in this same transaction.
-- Undo reverts exactly the logged rows (later merges first). The host's subject generation goes up on merge/undo so a
-- contradiction judgement made before it is not stored after it; moved fragments are queued for a rescan.
-- Local draft; production application requires separate user approval.
begin;

alter table knowledge.subject add column merged_into uuid references knowledge.subject(subject_id)
  check (merged_into <> subject_id);

create table knowledge.subject_generation (
  host_id text primary key references knowledge.host(host_id),
  generation bigint not null default 0 check (generation >= 0)
);

create table knowledge.subject_merge (
  merge_id uuid primary key default gen_random_uuid(),
  host_id text not null references knowledge.host(host_id),
  from_subject uuid not null references knowledge.subject(subject_id),
  into_subject uuid not null references knowledge.subject(subject_id),
  confirmation_id bigint not null references knowledge.confirmation_queue(confirmation_id),
  shown jsonb not null,              -- the two subject ids and names the user was asked about
  approved_quote text not null check (length(btrim(approved_quote)) > 0),
  locator text,
  state text not null default 'running' check (state in ('running', 'done', 'undoing', 'undone')),
  txid bigint not null default txid_current(),   -- the transaction allowed to move rows for this merge
  generation_after bigint,
  undo_quote text,
  created_at timestamptz not null default now(),
  undone_at timestamptz,
  check (from_subject <> into_subject),
  check (state <> 'undone' or (undo_quote is not null and undone_at is not null))
);
-- one approval merges once (a retried answer is refused, an undone merge is not re-run from the same question)
create unique index subject_merge_one_per_question on knowledge.subject_merge(confirmation_id);

create table knowledge.subject_merge_item (
  merge_id uuid not null references knowledge.subject_merge(merge_id),
  seq integer not null check (seq >= 0),
  tbl text not null check (tbl in ('subject_alias', 'subject', 'fragment', 'fragment_leaf')),
  row_key jsonb not null,            -- alias: {alias}; subject: {subject_id}; fragment: {id}; leaf: {fragment_id,revision,ord}
  old_subject uuid,                  -- subject.merged_into may be null before
  new_subject uuid not null,
  guard jsonb not null default '{}',  -- the row as merged (fragment revision; alias kind/evidence): undo refuses if it changed
  primary key (merge_id, seq)
);
-- an alias row moved by a merge: lookups resolve it to the survivor but never rewrite the text with the survivor's name
alter table knowledge.subject_alias add column merged_by uuid references knowledge.subject_merge(merge_id);

-- the subject generation a digestion judgement was made under (astra merge r2 P1-2: a cached partial-pass judgement made
-- before a merge must not be reused after it)
alter table knowledge.check_receipt add column subject_generation bigint;

create index subject_merge_item_fragment_idx on knowledge.subject_merge_item(merge_id, tbl, row_key);

create table knowledge.fragment_rescan (
  host_id text not null references knowledge.host(host_id),
  fragment_id uuid not null references knowledge.fragment(id),
  generation bigint not null,
  reason jsonb not null,
  requested_at timestamptz not null default now(),
  done_at timestamptz,
  outcome text,                      -- scanned | no_entry (a pre-ledger fragment: compared only as a neighbour)
  primary key (fragment_id, generation)
);
create index fragment_rescan_pending_idx on knowledge.fragment_rescan(host_id, requested_at) where done_at is null;

-- a question re-asked after a merge: one follow-up per withdrawn question (concurrent listings, astra merge r1 P1-7)
create unique index alias_conflict_one_followup on knowledge.confirmation_queue (((reason::jsonb) ->> 'previous'))
  where rule_id = 'alias_conflict' and reason like '%"previous"%';

alter table knowledge.subject_generation enable row level security;
alter table knowledge.subject_merge enable row level security;
alter table knowledge.subject_merge_item enable row level security;
alter table knowledge.fragment_rescan enable row level security;

-- astra merge r1 P0-1: a merge log is created only for a pending alias_conflict question of the same host about exactly
-- these two subjects, in the transaction that runs it; afterwards only the state machine may move
-- (running -> done in the same transaction, done -> undoing -> undone in the undo transaction); items only while running.
create or replace function knowledge.guard_subject_merge() returns trigger
language plpgsql set search_path = '' as $$
declare
  q record;
begin
  if tg_op = 'DELETE' then
    raise exception 'subject_merge rows are never deleted';
  elsif tg_op = 'INSERT' then
    select c.rule_id, c.status::text as status, c.reason, e.host_id into q
      from knowledge.confirmation_queue c join knowledge.ledger_entry e on e.entry_id = c.entry_id
     where c.confirmation_id = new.confirmation_id;
    if q is null or q.rule_id <> 'alias_conflict' or q.status <> 'pending' or q.host_id <> new.host_id
       or array[new.from_subject::text, new.into_subject::text] @> array[(q.reason::jsonb)->>'subject_id', (q.reason::jsonb)->>'other_id'] is not true
       or array[(q.reason::jsonb)->>'subject_id', (q.reason::jsonb)->>'other_id'] @> array[new.from_subject::text, new.into_subject::text] is not true
       or new.state <> 'running' or new.txid <> txid_current() then
      raise exception 'a subject merge needs the pending alias_conflict question about exactly these two subjects';
    end if;
    return new;
  end if;
  if (to_jsonb(new) - array['state', 'txid', 'generation_after', 'undo_quote', 'undone_at'])
     <> (to_jsonb(old) - array['state', 'txid', 'generation_after', 'undo_quote', 'undone_at']) then
    raise exception 'subject_merge: only its state may change';
  end if;
  if not ((old.state = 'running' and new.state = 'done' and old.txid = txid_current() and new.txid = old.txid)
       or (old.state = 'done' and new.state = 'undoing' and new.txid = txid_current())
       or (old.state = 'undoing' and new.state = 'undone' and old.txid = txid_current() and new.txid = old.txid)) then
    raise exception 'subject_merge: % -> % is not allowed here', old.state, new.state;
  end if;
  return new;
end;
$$;
create trigger subject_merge_guard before insert or update or delete on knowledge.subject_merge
  for each row execute function knowledge.guard_subject_merge();

create or replace function knowledge.guard_subject_merge_item() returns trigger
language plpgsql set search_path = '' as $$
begin
  if tg_op <> 'INSERT' then
    raise exception 'subject_merge_item rows are written once';
  end if;
  if not exists (select 1 from knowledge.subject_merge m where m.merge_id = new.merge_id and m.state = 'running'
                 and m.txid = txid_current()
                 -- astra merge r2 P1-3: a logged move is the approved one (from -> into), never another pair
                 and new.new_subject = m.into_subject
                 and (new.tbl = 'subject' or new.old_subject = m.from_subject)) then
    raise exception 'subject_merge_item: only the merge running in this transaction logs its own from -> into rows';
  end if;
  return new;
end;
$$;
create trigger subject_merge_item_guard before insert or update or delete on knowledge.subject_merge_item
  for each row execute function knowledge.guard_subject_merge_item();

-- the merge running in this transaction that logged this row, moving old -> new
create or replace function knowledge.merge_allows(p_host text, p_tbl text, p_key jsonb, p_old uuid, p_new uuid)
returns boolean language sql stable set search_path = '' as $$
  select exists (
    select 1 from knowledge.subject_merge m join knowledge.subject_merge_item i on i.merge_id = m.merge_id
     where m.merge_id = nullif(current_setting('knowledge.merge_id', true), '')::uuid
       and m.txid = txid_current() and m.host_id = p_host and i.tbl = p_tbl and i.row_key = p_key
       and ((m.state = 'running' and i.old_subject is not distinct from p_old and i.new_subject = p_new
             and p_old = m.from_subject and p_new = m.into_subject)
         or (m.state = 'undoing' and i.new_subject = p_old and i.old_subject is not distinct from p_new
             and p_old = m.into_subject and p_new = m.from_subject)));
$$;

create or replace function knowledge.track_fragment_change() returns trigger
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
    if new.revision = old.revision
       and (to_jsonb(new) - array['form', 'subject_id', 'updated_at']) = (to_jsonb(old) - array['form', 'subject_id', 'updated_at'])
       and (old.form is null or new.form is not distinct from old.form)
       and (old.subject_id is null or new.subject_id is not distinct from old.subject_id)
       and (new.form is not null or old.form is null) then
      return new;  -- filling empty structure only (0011)
    end if;
    -- 0014: a logged subject merge (or its undo) in this transaction moves only subject_id; nothing else may change
    if new.revision = old.revision and old.subject_id is not null and new.subject_id is not null
       and new.subject_id <> old.subject_id
       and (to_jsonb(new) - array['subject_id']) = (to_jsonb(old) - array['subject_id'])
       and knowledge.merge_allows(new.host_id, 'fragment', jsonb_build_object('id', new.id), old.subject_id, new.subject_id) then
      return new;
    end if;
    if new.revision <> old.revision + 1 then
      raise exception 'fragment revision must advance by exactly one (structure is filled once; changing it is a revision)';
    end if;
    insert into knowledge.fragment_history(fragment_id, revision, snapshot, actor, absorption_id, op)
    values (old.id, new.revision, to_jsonb(old), history_actor, absorption_setting::uuid,
            case when old.active and not new.active then 'deprecate' else 'update' end);
    new.updated_at := now();
  end if;
  return new;
end;
$$;

-- leaves are written once per revision; only a logged merge may move their subject
create or replace function knowledge.guard_fragment_leaf() returns trigger
language plpgsql set search_path = '' as $$
declare
  leaf_host text;
begin
  if tg_op = 'DELETE' then
    raise exception 'fragment_leaf DELETE is forbidden';
  end if;
  select host_id into leaf_host from knowledge.fragment where id = old.fragment_id;
  if old.subject_id is not null and new.subject_id is not null and new.subject_id <> old.subject_id
     and (to_jsonb(new) - array['subject_id']) = (to_jsonb(old) - array['subject_id'])
     and knowledge.merge_allows(leaf_host, 'fragment_leaf',
           jsonb_build_object('fragment_id', old.fragment_id, 'revision', old.revision, 'ord', old.ord),
           old.subject_id, new.subject_id) then
    return new;
  end if;
  raise exception 'fragment_leaf rows are written once; only a logged subject merge may move subject_id';
end;
$$;
create trigger fragment_leaf_guard before update or delete on knowledge.fragment_leaf
  for each row execute function knowledge.guard_fragment_leaf();

commit;
