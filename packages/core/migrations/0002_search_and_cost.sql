-- Local test only; production application requires separate user approval.
begin;

alter table knowledge.check_receipt
  add column input_tokens integer check (input_tokens >= 0),
  add column output_tokens integer check (output_tokens >= 0),
  add column cost_usd numeric(12,6) check (cost_usd >= 0),
  add column usage_source text;

create function knowledge.search_fragments(
  p_host text, p_query text, p_limit int default 8, p_cross_hosts text[] default null
) returns table (
  host_id text, id uuid, alias text, kind knowledge.fragment_kind, text text,
  keywords text[], group_id text, confidence knowledge.fragment_confidence,
  revision integer, score double precision, evidence_quote text
)
language sql stable security invoker set search_path = '' as $$
  select f.host_id, f.id, f.alias, f.kind, f.text, f.keywords, f.group_id,
         f.confidence, f.revision, ranked.score,
         f.source->'evidence_refs'->0->>'quote' as evidence_quote
  from knowledge.fragment as f
  cross join lateral (
    select extensions.similarity(f.text, p_query)::double precision +
           coalesce((select count(*)::double precision * 0.25
                     from unnest(f.keywords) as k(keyword)
                     where nullif(k.keyword, '') is not null
                       and position(lower(k.keyword) in lower(p_query)) > 0), 0) as score
  ) as ranked
  where f.active and p_query is not null and (
    f.host_id = p_host or (
      f.host_id = any(p_cross_hosts)
      and exists (select 1 from knowledge.host as h
                  where h.host_id = f.host_id and h.cross_search_allowed)
    )
  ) and ranked.score > 0
  order by ranked.score desc, f.host_id, f.id
  limit greatest(coalesce(p_limit, 0), 0);
$$;

create function knowledge.expand_group(p_host text, p_group_id text)
returns table (
  host_id text, id uuid, alias text, kind knowledge.fragment_kind, text text,
  keywords text[], group_id text, confidence knowledge.fragment_confidence,
  revision integer, score double precision, evidence_quote text
)
language sql stable security invoker set search_path = '' as $$
  select f.host_id, f.id, f.alias, f.kind, f.text, f.keywords, f.group_id,
         f.confidence, f.revision, null::double precision,
         f.source->'evidence_refs'->0->>'quote'
  from knowledge.fragment as f
  where f.host_id = p_host and f.group_id = p_group_id
    and p_group_id is not null and f.active
  order by f.id;
$$;

revoke all on function knowledge.search_fragments(text,text,int,text[]) from anon, authenticated, public;
revoke all on function knowledge.expand_group(text,text) from anon, authenticated, public;
commit;
