-- G16 feature-level subjects. Worktree-local proposal; never apply to remote/live DB.
-- Fresh pilot is empty at this point. Existing untyped rows MUST be explicitly classified
-- before this migration; NOT NULL intentionally fails rather than guessing a default type.
begin;
alter table knowledge.subject add column if not exists subject_type text;
do $$
begin
  if not exists (select 1 from pg_constraint where conrelid='knowledge.subject'::regclass and conname='subject_type_fixed') then
    alter table knowledge.subject add constraint subject_type_fixed check
      (subject_type in ('기능','화면','API','데이터','규칙/정책','작업 흐름','외부 연동'));
  end if;
  if not exists (select 1 from pg_constraint where conrelid='knowledge.subject'::regclass and conname='subject_description_present') then
    alter table knowledge.subject add constraint subject_description_present check (length(btrim(description))>0);
  end if;
end $$;
alter table knowledge.subject alter column subject_type set not null;
commit;
