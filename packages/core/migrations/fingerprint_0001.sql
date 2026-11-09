-- Structural + seed fingerprint of the knowledge schema (same query local and remote).
select 'columns' as part, md5(string_agg(table_name||'.'||column_name||':'||data_type||':'||udt_name||':'||is_nullable||':'||coalesce(column_default,''), '|' order by table_name, ordinal_position)) as md5
  from information_schema.columns where table_schema='knowledge'
union all
select 'constraints', md5(string_agg(conrelid::regclass::text||':'||conname||':'||pg_get_constraintdef(oid), '|' order by conrelid::regclass::text, conname))
  from pg_constraint where connamespace='knowledge'::regnamespace
union all
select 'indexes', md5(string_agg(indexname||':'||indexdef, '|' order by indexname))
  from pg_indexes where schemaname='knowledge'
union all
select 'functions', md5(string_agg(p.proname||':'||pg_get_functiondef(p.oid), '|' order by p.proname))
  from pg_proc p where p.pronamespace='knowledge'::regnamespace
union all
select 'triggers', md5(string_agg(tgrelid::regclass::text||':'||tgname||':'||pg_get_triggerdef(oid), '|' order by tgrelid::regclass::text, tgname))
  from pg_trigger where not tgisinternal and tgrelid in (select oid from pg_class where relnamespace='knowledge'::regnamespace)
union all
select 'enums', md5(string_agg(t.typname||':'||e.enumlabel, '|' order by t.typname, e.enumsortorder))
  from pg_type t join pg_enum e on e.enumtypid=t.oid where t.typnamespace='knowledge'::regnamespace
union all
select 'rls', md5(string_agg(relname||':'||relrowsecurity, '|' order by relname))
  from pg_class where relnamespace='knowledge'::regnamespace and relkind='r'
union all
select 'seed_policy', md5(string_agg(coalesce(host_id,'-')||':'||version||':'||ordinal||':'||rule_id||':'||when_json::text||':'||then_action, '|' order by ordinal))
  from knowledge.acceptance_policy
union all
select 'seed_templates', md5(string_agg(template_id||':'||template_version||':'||questions::text||':'||status, '|' order by template_id))
  from knowledge.question_template
union all
select 'anon_table_grants', count(*)::text
  from information_schema.role_table_grants where table_schema='knowledge' and grantee in ('anon','authenticated','PUBLIC')
order by 1;
