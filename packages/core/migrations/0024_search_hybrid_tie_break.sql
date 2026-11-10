-- 0024: deterministic search_hybrid tie-break (Task335, stage-workspace replay finding 2026-11-04).
-- Same signature/return type/body as 0008 except the final ORDER BY: exact RRF ties are broken by
-- vector similarity instead of the random fragment UUID. CREATE OR REPLACE keeps existing privileges
-- (0008 revoked anon/authenticated/public).
create or replace function knowledge.search_hybrid(
  p_host text, p_query text, p_query_embedding extensions.vector, p_model_id text,
  p_limit int default 8, p_cross_hosts text[] default null, p_k int default 60,
  p_min_similarity double precision default null, p_exclude_ids uuid[] default null,
  p_embed_variant text default 'plain'
) returns table (
  host_id text, id uuid, alias text, kind knowledge.fragment_kind, text text,
  keywords text[], group_id text, confidence knowledge.fragment_confidence,
  revision integer, score double precision, evidence_quote text,
  vector_rank bigint, text_rank bigint, vector_similarity double precision
)
language sql stable security invoker set search_path = '' as $$
  with vector_hits as (
    select f.id, (1 - (e.embedding OPERATOR(extensions.<=>) p_query_embedding))::double precision as similarity,
      row_number() over (
        order by e.embedding OPERATOR(extensions.<=>) p_query_embedding, f.host_id, f.id
      ) as rank
    from knowledge.fragment f
    join knowledge.fragment_embedding e on e.fragment_id = f.id
      and e.revision = f.revision and e.model_id = p_model_id and e.embed_variant = p_embed_variant
    where f.active and p_query_embedding is not null
      and not (f.id = any(coalesce(p_exclude_ids, array[]::uuid[])))
      and (p_min_similarity is null or 1 - (e.embedding OPERATOR(extensions.<=>) p_query_embedding) >= p_min_similarity)
      and (
      f.host_id = p_host or (f.host_id = any(p_cross_hosts) and exists (
        select 1 from knowledge.host h where h.host_id = f.host_id and h.cross_search_allowed
      ))
    )
  ), text_hits as (
    select t.id, row_number() over (order by t.score desc, t.host_id, t.id) as rank
    from knowledge.search_fragments(p_host, p_query, 2147483647, p_cross_hosts) t
    where not (t.id = any(coalesce(p_exclude_ids, array[]::uuid[])))
  ), fused as (
    select coalesce(v.id, t.id) as id, v.rank as vector_rank, t.rank as text_rank,
      v.similarity as vector_similarity,
      (case when v.rank is not null then 1.0 / (p_k + v.rank) else 0 end +
       case when t.rank is not null then 1.0 / (p_k + t.rank) else 0 end)::double precision as score
    from vector_hits v full join text_hits t using (id)
  )
  select f.host_id, f.id, f.alias, f.kind, f.text, f.keywords, f.group_id,
         f.confidence, f.revision, fused.score,
         f.source->'evidence_refs'->0->>'quote', fused.vector_rank, fused.text_rank, fused.vector_similarity
  from fused join knowledge.fragment f on f.id = fused.id
  where p_k > 0
  -- RRF fuses ranks only, so candidates that swap places between the two lists tie exactly
  -- (1/(k+1)+1/(k+2) both ways). Break such ties by the measured vector similarity, not by a
  -- random UUID; host/id stay as the final deterministic fallback.
  order by fused.score desc, fused.vector_similarity desc nulls last, f.host_id, f.id
  limit greatest(coalesce(p_limit, 0), 0);
$$;
