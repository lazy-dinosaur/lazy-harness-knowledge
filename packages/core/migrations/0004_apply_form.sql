alter table knowledge.work_unit
  add column baseline_history_id bigint,
  add column baseline_code_ref text,
  add column baseline_at timestamptz;

create function knowledge.changed_since(p_host text, p_history_id bigint)
returns table(fragment_id uuid, revision int, op text, changed_at timestamptz, text text, active boolean)
language sql stable security invoker set search_path = '' as $$
  select distinct on (h.fragment_id)
    h.fragment_id, f.revision, h.op, h.changed_at, f.text, f.active
  from knowledge.fragment_history h
  join knowledge.fragment f on f.id = h.fragment_id
  where f.host_id = p_host and h.history_id > p_history_id
  order by h.fragment_id, h.history_id desc;
$$;
revoke all on function knowledge.changed_since(text, bigint) from anon, authenticated, public;
