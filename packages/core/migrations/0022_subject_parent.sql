-- Worktree-local empty-DB pilot only. Never apply to the remote/live database.
begin;
alter table knowledge.subject add column if not exists parent_subject_id uuid
  references knowledge.subject(subject_id);

-- Validate both the changed row and its children: parent edits/merges cannot
-- silently turn a valid two-level tree into a cross-domain or three-level tree.
create or replace function knowledge.check_subject_parent() returns trigger
language plpgsql as $$
begin
  perform pg_advisory_xact_lock(hashtextextended('lh-subject-parent', 0));
  if exists (
    select 1 from knowledge.subject c
    left join knowledge.subject p on p.subject_id=c.parent_subject_id
    where c.parent_subject_id is not null and (
      p.subject_id is null or c.subject_id=p.subject_id
      or c.host_id<>p.host_id or c.domain<>p.domain
      or p.parent_subject_id is not null or p.merged_into is not null
      or p.subject_type not in ('기능','작업 흐름','외부 연동','화면'))
  ) then
    raise exception 'E_SUBJECT_PARENT: same host/domain root parent, allowed type, depth <= 2 required';
  end if;
  return null;
end $$;
drop trigger if exists subject_parent_guard on knowledge.subject;
create constraint trigger subject_parent_guard after insert or update on knowledge.subject
  -- Domain relocation/undo moves rows individually; validate the final transaction graph.
  deferrable initially deferred for each row execute function knowledge.check_subject_parent();
commit;
