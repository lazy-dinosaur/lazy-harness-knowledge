-- 0013: declared aliases (2026-10-03, user 'a', stage 1 of resolving aliases in the subject dictionary).
-- A parent may declare other names of the same thing on a fact (aliases: [...]); digestion registers each as a 'declared'
-- alias of the fact's subject, with the fragment and revision it came from. 'spelling' aliases are the existing
-- spelling/tail variants. Stage 1 never merges two existing subjects (a conflict is reported for approval).
-- Local draft; production application requires separate user approval.
begin;

alter table knowledge.subject_alias add column kind text not null default 'spelling'
  check (kind in ('spelling', 'declared'));
alter table knowledge.subject_alias add column source_fragment_id uuid references knowledge.fragment(id);
alter table knowledge.subject_alias add column source_revision integer check (source_revision > 0);
alter table knowledge.subject_alias add constraint declared_alias_has_source
  check (kind <> 'declared' or (source_fragment_id is not null and source_revision is not null));

commit;
