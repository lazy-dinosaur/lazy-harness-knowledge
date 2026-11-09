-- 0012: a contradiction's answer is not its resolution (astra direction review P1, cross-review rounds, 2026-10-01).
-- status/answer stay the question state (asked / answered / expired) and are never erased.
-- resolution is the state of the contradiction itself:
--   open        the two canonical facts contradict (as last judged at the revisions in reason)
--   recheck     a side may have changed since it was judged; the poller judges the pair again
--   resolved    judged no longer contradicting at stated revisions, a side retired, or the user said it is not one
--   superseded  asked again as a new question (new confirmation_id) with the current texts; this row keeps its answer
-- An answer alone never resolves it: the fix record changes a side, the rejudge resolves it.
-- Deploy: the code that writes resolution needs this migration first (stop the digester, apply, then run the code).
-- Local draft; production application requires separate user approval.
begin;

alter table knowledge.confirmation_queue add column resolution text
  check (resolution in ('open', 'recheck', 'resolved', 'superseded'));
alter table knowledge.confirmation_queue add column resolution_evidence jsonb;
alter table knowledge.confirmation_queue add column resolved_at timestamptz;
alter table knowledge.confirmation_queue add column rechecked_at timestamptz;

-- existing rows: questions still asked are open; answered or expired ones are judged again (their closing proved nothing)
update knowledge.confirmation_queue
   set resolution = case when status = 'pending' then 'open' else 'recheck' end
 where rule_id = 'canon_contradiction';

alter table knowledge.confirmation_queue add constraint contradiction_has_resolution
  check ((rule_id = 'canon_contradiction') = (resolution is not null));
alter table knowledge.confirmation_queue add constraint resolved_has_evidence
  check (resolution not in ('resolved', 'superseded')
         or (resolution_evidence is not null and resolved_at is not null));

-- one live question per (fact, other fragment): concurrent scans cannot insert the same contradiction twice
create unique index confirmation_queue_one_live_contradiction on knowledge.confirmation_queue
  (entry_id, fact_index, ((reason::jsonb) ->> 'with'))
  where rule_id = 'canon_contradiction' and resolution in ('open', 'recheck');

create index confirmation_queue_unresolved on knowledge.confirmation_queue (rechecked_at, confirmation_id)
  where rule_id = 'canon_contradiction' and resolution in ('open', 'recheck');

commit;
