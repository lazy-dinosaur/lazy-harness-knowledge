-- 0011: structure is filled once (astra direction review P1, 2026-10-01).
-- 0010 let any UPDATE that changed only form/subject_id/updated_at keep the revision and write no history. form is meaning
-- (AND->OR, a dropped condition) and subject_id routes contradictions, so that exemption could change meaning silently.
-- Now the exemption holds only while filling empty fields: old.form is null, and subject_id is either unchanged or
-- filled from null. Changing a set form or subject_id needs revision+1 and leaves a history row like any change.
-- Local draft; production application requires separate user approval.
begin;

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
      return new;  -- filling empty structure only
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

commit;
