-- 0007: harness rules are code, not DB rows (schema-delta '답변 끝 통합 판정 — 하네스 규칙은 하드코딩', 2026-09-29).
-- Receipts may now refer to hard-coded harness rules (origin='harness', id 'H-*') that have no rules.rule row.
alter table rules.judgement_receipt drop constraint judgement_receipt_rule_id_fkey;
alter table rules.judgement_receipt add column origin text not null default 'project' check (origin in ('project', 'harness'));
alter table rules.judgement_receipt add constraint judgement_receipt_origin_id check ((origin = 'harness') = (rule_id like 'H-%'));
-- retire the base rules that were stored as rows (h-*); history keeps what they were
update rules.rule set status = 'deleted', version = version + 1, updated_at = now() where host_id is null and status = 'active';
insert into rules.rule_history(rule_id, version, op, snapshot, source)
select rule_id, version, 'delete', jsonb_build_object('when', when_text, 'must', must_text),
       '{"quote": "harness rules are hard-coded, not DB rows (user decision 2026-09-29)"}'::jsonb
from rules.rule r where host_id is null and status = 'deleted'
  and not exists (select 1 from rules.rule_history h where h.rule_id = r.rule_id and h.op = 'delete');
