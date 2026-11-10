-- G16 II: durable per-fact routing; ledger remains the log. Local application only.
begin;
alter table knowledge.domain_type add column generation bigint not null default 0;
-- A work unit can span partitions; retain the host FK, not the obsolete single-domain FK.
alter table knowledge.work_unit add constraint work_unit_host_key unique(work_unit_id,host_id);
alter table knowledge.ledger_entry drop constraint ledger_entry_work_unit_id_host_id_partition_key_fkey;
alter table knowledge.ledger_entry add constraint ledger_entry_work_unit_host_fkey
 foreign key(work_unit_id,host_id) references knowledge.work_unit(work_unit_id,host_id);
create table knowledge.digest_part (
 entry_id uuid not null references knowledge.ledger_entry(entry_id),
 fact_index integer not null check(fact_index>=0),
 host_id text not null, domain text not null,
 created_at timestamptz not null default now(),
 primary key(entry_id,fact_index),
 foreign key(host_id,domain) references knowledge.domain_type(host_id,domain)
);
create index digest_part_partition on knowledge.digest_part(host_id,domain);
create table knowledge.merge_judgement (
 id bigint generated always as identity primary key,
 host_id text not null references knowledge.host(host_id),
 kind text not null check(kind in ('subject','domain')),
 pair text[] not null, fragment_fingerprint text not null,
 verdict jsonb not null, holds integer not null, review_list boolean not null,
 created_at timestamptz not null default now()
);
create trigger merge_judgement_immutable before update or delete on knowledge.merge_judgement
 for each row execute function knowledge.reject_history_mutation();
alter table knowledge.digest_part enable row level security;
alter table knowledge.merge_judgement enable row level security;
create table knowledge.domain_move (
 merge_id uuid not null references knowledge.domain_merge(merge_id), seq integer not null,
 tbl text not null check(tbl in ('subject','subject_alias','fragment','digest_part')),
 row_key jsonb not null, before_row jsonb not null, after_row jsonb not null,
 txid bigint not null default txid_current(), primary key(merge_id,seq)
);
create trigger domain_move_immutable before update or delete on knowledge.domain_move
 for each row execute function knowledge.reject_history_mutation();
create table knowledge.domain_subject_merge (
 domain_merge_id uuid not null references knowledge.domain_merge(merge_id),
 subject_merge_id uuid not null references knowledge.subject_merge(merge_id),
 primary key(domain_merge_id,subject_merge_id)
);
create trigger domain_subject_merge_immutable before update or delete on knowledge.domain_subject_merge
 for each row execute function knowledge.reject_history_mutation();
alter table knowledge.domain_move enable row level security;
alter table knowledge.domain_subject_merge enable row level security;
-- Exact logged structural move only. Text, form, identity and revision stay unchanged.
do $$ declare def text; begin
 select pg_get_functiondef('knowledge.track_fragment_change()'::regprocedure) into def;
 def := replace(def, 'if new.revision <> old.revision + 1 then', $branch$
 if exists(select 1 from knowledge.domain_move i join knowledge.domain_merge m on m.merge_id=i.merge_id
   where i.tbl='fragment' and m.host_id=new.host_id
   and i.merge_id=nullif(current_setting('knowledge.domain_move_id',true),'')::uuid
   and ((i.txid=txid_current() and i.before_row=to_jsonb(old) and i.after_row=to_jsonb(new))
     or (current_setting('knowledge.domain_move_undo',true)='on' and m.undone_at is null
         and i.after_row=to_jsonb(old) and i.before_row=to_jsonb(new)))) then return new; end if;
 if new.revision <> old.revision + 1 then
 $branch$);
 execute def;
end $$;
commit;
