-- G16 step ①. Apply with migrate_domain_subject.py for atomic DDL + data.
-- No remote apply approval. The data runner validates the NOT VALID legacy constraints.
alter table knowledge.domain_type add column if not exists description_evidence jsonb not null default '[]';
alter table knowledge.domain_type add column if not exists description_updated_at timestamptz;
alter table knowledge.domain_type add column if not exists maintenance_pending boolean not null default false;
alter table knowledge.subject add column if not exists domain text;
alter table knowledge.subject add column if not exists description text not null default '';
alter table knowledge.subject add column if not exists description_evidence jsonb not null default '[]';
alter table knowledge.subject add column if not exists description_updated_at timestamptz;
alter table knowledge.subject_alias add column if not exists domain text;
alter table knowledge.subject drop constraint if exists subject_host_id_name_key;
alter table knowledge.subject_alias drop constraint if exists subject_alias_pkey;
create unique index if not exists subject_domain_name_key on knowledge.subject(host_id, domain, name);
create unique index if not exists subject_domain_id_key on knowledge.subject(host_id, domain, subject_id);
create unique index if not exists subject_alias_domain_key on knowledge.subject_alias(host_id, domain, alias);

create table if not exists knowledge.domain_alias (
  host_id text not null, alias text not null check (length(btrim(alias))>0), domain text not null,
  evidence jsonb not null default '[]', created_at timestamptz not null default now(),
  primary key(host_id, alias), foreign key(host_id, domain) references knowledge.domain_type(host_id, domain)
);
create table if not exists knowledge.domain_embedding (
  host_id text not null, domain text not null, model_id text not null,
  embedding extensions.vector not null, created_at timestamptz not null default now(),
  primary key(host_id, domain, model_id), foreign key(host_id, domain) references knowledge.domain_type(host_id, domain)
);
create table if not exists knowledge.g16_subject_map (
  old_subject uuid not null, domain text not null, new_subject uuid not null references knowledge.subject(subject_id),
  rule text not null check(rule in ('a','b','c','d')), primary key(old_subject,domain)
);
create table if not exists knowledge.g16_move (
  tbl text not null check(tbl in ('fragment','fragment_leaf')), row_key jsonb not null,
  host_id text not null, old_subject uuid not null, new_subject uuid not null,
  txid bigint not null default txid_current(), primary key(tbl,row_key)
);
-- A projection of legacy receipts, not a rewrite of the append-only 0014 history.
create table if not exists knowledge.g16_merge_item (
  merge_id uuid not null, seq integer not null, domain text not null,
  tbl text not null, row_key jsonb not null, old_subject uuid, new_subject uuid not null,
  guard jsonb not null, primary key(merge_id,seq,domain),
  foreign key(merge_id,seq) references knowledge.subject_merge_item(merge_id,seq)
);
create trigger g16_merge_item_immutable before update or delete on knowledge.g16_merge_item
for each row execute function knowledge.reject_history_mutation();
create table if not exists knowledge.g16_migration (
  version text primary key, report jsonb not null, created_at timestamptz not null default now()
);

-- Compatibility for direct subject inserts: create the domain FIRST, never hold for missing evidence.
create or replace function knowledge.subject_domain_first() returns trigger
language plpgsql set search_path='' as $$
begin
  if new.domain is null or btrim(new.domain)='' then
    new.domain := 'subject:' || btrim(new.name);
    insert into knowledge.domain_type(host_id,domain,maintenance_pending)
      values(new.host_id,new.domain,true) on conflict do nothing;
  end if;
  return new;
end $$;
drop trigger if exists subject_domain_first on knowledge.subject;
create trigger subject_domain_first before insert or update on knowledge.subject
for each row execute function knowledge.subject_domain_first();
create or replace function knowledge.alias_domain_scope() returns trigger
language plpgsql set search_path='' as $$
declare d text;
begin
  select domain into d from knowledge.subject where subject_id=new.subject_id and host_id=new.host_id;
  if d is null then raise exception 'alias requires a same-host domain subject'; end if;
  if new.domain is null then new.domain:=d; end if;
  if new.domain<>d then raise exception 'alias domain differs from subject domain'; end if;
  return new;
end $$;
drop trigger if exists alias_domain_scope on knowledge.subject_alias;
create trigger alias_domain_scope before insert or update on knowledge.subject_alias
for each row execute function knowledge.alias_domain_scope();

-- Leaf host is derived from its immutable parent, not its subject's domain.
create or replace function knowledge.leaf_same_host() returns trigger
language plpgsql set search_path='' as $$
begin
  if new.subject_id is not null and not exists (
    select 1 from knowledge.fragment f join knowledge.subject s on s.host_id=f.host_id
    where f.id=new.fragment_id and s.subject_id=new.subject_id
  ) then raise exception 'leaf subject must belong to the fragment host'; end if;
  return new;
end $$;
drop trigger if exists leaf_same_host on knowledge.fragment_leaf;
create trigger leaf_same_host before insert or update on knowledge.fragment_leaf
for each row execute function knowledge.leaf_same_host();

-- Add constraints once; legacy rows are checked after the transactional backfill.
do $$ begin
  if not exists(select 1 from pg_constraint where conname='g16_subject_domain_fk') then
    alter table knowledge.subject add constraint g16_subject_domain_fk foreign key(host_id,domain)
      references knowledge.domain_type(host_id,domain) not valid;
    alter table knowledge.subject add constraint g16_subject_domain_required check(domain is not null and btrim(domain)<>'') not valid;
    alter table knowledge.subject_alias add constraint g16_alias_domain_required check(domain is not null) not valid;
    alter table knowledge.subject_alias add constraint g16_alias_domain_fk foreign key(host_id,domain,subject_id)
      references knowledge.subject(host_id,domain,subject_id) deferrable initially deferred not valid;
    alter table knowledge.fragment add constraint g16_fragment_subject_fk foreign key(host_id,domain,subject_id)
      references knowledge.subject(host_id,domain,subject_id) deferrable initially deferred not valid;
    alter table knowledge.subject add constraint g16_merge_domain_fk foreign key(host_id,domain,merged_into)
      references knowledge.subject(host_id,domain,subject_id) deferrable initially deferred not valid;
  end if;
end $$;

-- Preserve 0014's exact-row/transaction guards; only logged G16 structural ID moves are added.
do $$ declare def text; begin
  select pg_get_functiondef('knowledge.merge_allows(text,text,jsonb,uuid,uuid)'::regprocedure) into def;
  if position('knowledge.g16_move' in def)=0 then
    def:=replace(def, 'select exists (', 'select exists (select 1 from knowledge.g16_move g where g.txid=txid_current() and g.host_id=p_host and g.tbl=p_tbl and g.row_key=p_key and g.old_subject=p_old and g.new_subject=p_new) or exists (');
    execute def;
  end if;
end $$;

alter table knowledge.subject_merge add column if not exists actor text not null default 'user';
alter table knowledge.subject_merge add column if not exists reason jsonb not null default '{}';
alter table knowledge.subject_merge add column if not exists evidence jsonb not null default '[]';
alter table knowledge.subject_merge alter column confirmation_id drop not null;
-- Existing manual receipts keep their pending-question proof; Luna receipts need explicit reasoning/evidence.
do $$ declare def text; begin
  select pg_get_functiondef('knowledge.guard_subject_merge()'::regprocedure) into def;
  if position('luna-auto' in def)=0 then
    def:=replace(def, 'elsif tg_op = ''INSERT'' then', $branch$elsif tg_op = 'INSERT' then
    if not exists(select 1 from knowledge.subject a join knowledge.subject b
      on a.host_id=b.host_id and a.domain=b.domain
      where a.subject_id=new.from_subject and b.subject_id=new.into_subject and a.host_id=new.host_id) then
      raise exception 'subject merge requires same host and domain';
    end if;
    if new.actor='luna-auto' then
      if new.confirmation_id is not null or new.reason='{}'::jsonb or jsonb_typeof(new.reason)<>'object'
         or new.evidence='[]'::jsonb or jsonb_typeof(new.evidence)<>'array'
         or new.state<>'running' or new.txid<>txid_current() then
        raise exception 'automatic merge needs reason, evidence and a running transaction';
      end if;
      return new;
    elsif new.actor<>'user' then raise exception 'unknown merge actor'; end if;
$branch$);
    execute def;
  end if;
end $$;

-- Domain routing redirects, with exact snapshot CAS undo. Does not conflate subjects across domains.
create table if not exists knowledge.domain_merge (
  merge_id uuid primary key default gen_random_uuid(), host_id text not null,
  from_domain text not null, into_domain text not null, actor text not null check(actor in ('user','luna-auto')),
  reason jsonb not null check(jsonb_typeof(reason)='object' and reason<>'{}'),
  evidence jsonb not null check(jsonb_typeof(evidence)='array' and evidence<>'[]'),
  before_row jsonb not null, after_row jsonb not null,
  created_at timestamptz not null default now(), undone_at timestamptz, undo_reason text,
  foreign key(host_id,from_domain) references knowledge.domain_type(host_id,domain),
  foreign key(host_id,into_domain) references knowledge.domain_type(host_id,domain),
  check(from_domain<>into_domain), check((undone_at is null)=(undo_reason is null))
);
create or replace function knowledge.guard_domain_merge_receipt() returns trigger
language plpgsql set search_path='' as $$
begin
  if tg_op='DELETE' then raise exception 'domain merge receipt is immutable'; end if;
  if (to_jsonb(new)-array['undone_at','undo_reason']) is distinct from
     (to_jsonb(old)-array['undone_at','undo_reason'])
     or old.undone_at is not null or new.undone_at is null
     or coalesce(btrim(new.undo_reason),'')='' then
    raise exception 'domain merge receipt is immutable except one undo';
  end if;
  if not exists(select 1 from knowledge.domain_type d where d.host_id=old.host_id
    and d.domain=old.from_domain and to_jsonb(d)=old.before_row) then
    raise exception 'domain merge undo must restore its exact snapshot';
  end if;
  return new;
end $$;
create trigger domain_merge_receipt_guard before update or delete on knowledge.domain_merge
for each row execute function knowledge.guard_domain_merge_receipt();
create or replace function knowledge.undo_domain_merge(p_id uuid,p_reason text) returns void
language plpgsql set search_path='' as $$
declare m knowledge.domain_merge; d jsonb;
begin
  if coalesce(btrim(p_reason),'')='' then raise exception 'undo reason required'; end if;
  select * into strict m from knowledge.domain_merge where merge_id=p_id for update;
  perform pg_advisory_xact_lock(hashtextextended('lh-dict/'||m.host_id,0));
  if m.undone_at is not null then raise exception 'already undone'; end if;
  select to_jsonb(x) into d from knowledge.domain_type x where host_id=m.host_id and domain=m.from_domain for update;
  if d is distinct from m.after_row then raise exception 'domain changed after merge'; end if;
  if exists(select 1 from knowledge.domain_merge n where n.host_id=m.host_id and n.undone_at is null
      and n.created_at>m.created_at and (n.from_domain in (m.from_domain,m.into_domain) or n.into_domain in (m.from_domain,m.into_domain))) then
    raise exception 'undo later domain merges first';
  end if;
  update knowledge.domain_type set status=m.before_row->>'status', merged_into=m.before_row->>'merged_into',
    updated_at=(m.before_row->>'updated_at')::timestamptz where host_id=m.host_id and domain=m.from_domain;
  update knowledge.domain_merge set undone_at=now(),undo_reason=p_reason where merge_id=p_id;
end $$;

-- The original merge must be undoing in THIS transaction; a split projection grants
-- only its exact logged row and mapped old/new IDs. Existing revision guards remain.
create or replace function knowledge.merge_allows(p_host text,p_tbl text,p_key jsonb,p_old uuid,p_new uuid)
returns boolean language sql stable set search_path='' as $$
  select exists(select 1 from knowledge.g16_move g where g.txid=txid_current()
    and g.host_id=p_host and g.tbl=p_tbl and g.row_key=p_key and g.old_subject=p_old and g.new_subject=p_new)
  or exists(select 1 from knowledge.subject_merge m join knowledge.subject_merge_item i using(merge_id)
    where m.merge_id=nullif(current_setting('knowledge.merge_id',true),'')::uuid
      and m.txid=txid_current() and m.host_id=p_host and i.tbl=p_tbl and i.row_key=p_key
      and ((m.state='running' and i.old_subject is not distinct from p_old and i.new_subject=p_new
            and p_old=m.from_subject and p_new=m.into_subject)
        or (m.state='undoing' and i.new_subject=p_old and i.old_subject is not distinct from p_new
            and p_old=m.into_subject and p_new=m.from_subject)))
  or exists(select 1 from knowledge.subject_merge m join knowledge.g16_merge_item i using(merge_id)
    where m.merge_id=nullif(current_setting('knowledge.merge_id',true),'')::uuid
      and m.state='undoing' and m.txid=txid_current() and m.host_id=p_host
      and i.tbl=p_tbl and i.row_key=p_key and i.new_subject=p_old and i.old_subject=p_new);
$$;
alter table knowledge.g16_merge_item enable row level security;
alter table knowledge.domain_alias enable row level security;
alter table knowledge.domain_embedding enable row level security;
alter table knowledge.domain_merge enable row level security;
alter table knowledge.g16_subject_map enable row level security;
alter table knowledge.g16_move enable row level security;
alter table knowledge.g16_migration enable row level security;
