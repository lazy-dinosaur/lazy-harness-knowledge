#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
name=lh-kdb-test-01
image=public.ecr.aws/supabase/postgres:17.6.1.134
log=migrations/test_0001_local.log
exec > >(tee "$log") 2>&1
if [[ -n "$(docker ps -a --filter "name=^/${name}$" --format '{{.ID}}')" ]]; then
  echo "STOP: $name already exists; no container touched"; exit 1
fi
if [[ "$(docker image inspect "$image" --format '{{.Id}}')" != sha256:425db1e29d960515f1dc8882577abee027f060d1b2d374d0496efdb21a39fae6 ]]; then
  echo 'STOP: local image id mismatch'; exit 1
fi
cleanup() {
  if [[ "${1:-0}" != 0 ]]; then docker logs --tail 70 "$name" || true; fi
  docker rm -f -v "$name" || true
  echo "cleanup docker ps -a --filter name=lh-kdb-test:"
  docker ps -a --filter name=lh-kdb-test --format '{{.Names}}'
}
trap 'cleanup "$?"' EXIT
docker run -d --rm --name "$name" --network none -e POSTGRES_PASSWORD=localtest "$image"
psql_db() { docker exec -i "$name" psql -X -v ON_ERROR_STOP=1 -U postgres -d postgres "$@"; }
for i in $(seq 1 120); do
  if [[ "$(psql_db -Atc "select to_regnamespace('extensions') is not null and exists(select 1 from pg_roles where rolname='anon') and exists(select 1 from pg_roles where rolname='authenticated')" 2>/dev/null || true)" == t ]]; then break; fi
  sleep 1
done
[[ "$i" -lt 120 ]] || { echo 'FAIL initialization wait'; exit 1; }
# Supabase init briefly exposes roles/schema via its temporary server before a restart.
for i in $(seq 1 120); do
  if [[ "$(docker inspect "$name" --format '{{.State.Health.Status}}' 2>/dev/null || true)" == healthy ]]; then break; fi
  sleep 1
done
[[ "$i" -lt 120 ]] || { echo 'FAIL post-init health wait'; exit 1; }
psql_db -f - < migrations/0001_knowledge_init.sql
echo 'a expected tables=13 RLS=13 policies=10 templates=3'
psql_db -Atc "select 'a actual tables='||count(*)||' RLS='||count(*) filter(where c.relrowsecurity) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='knowledge' and c.relkind='r'; select 'a actual policies='||(select count(*) from knowledge.acceptance_policy)||' templates='||(select count(*) from knowledge.question_template);"
[[ "$(psql_db -Atc "select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='knowledge' and c.relkind='r' and c.relrowsecurity")" == 13 ]]
[[ "$(psql_db -Atc 'select count(*) from knowledge.acceptance_policy')" == 10 ]]
[[ "$(psql_db -Atc 'select count(*) from knowledge.question_template')" == 3 ]]
negative() {
  local label=$1 sql=$2 output
  if output=$(psql_db -c "$sql" 2>&1); then echo "$label FAIL expected rejection: $output"; exit 1; fi
  [[ "$output" == *ERROR:* ]] || { echo "$label FAIL non-SQL error: $output"; exit 1; }
  echo "$label PASS expected rejection; actual: $(echo "$output" | grep 'ERROR:' | head -1)"
}
positive() { local label=$1 sql=$2; psql_db -Atc "$sql"; echo "$label PASS expected success; actual success"; }
negative b "set role anon; select * from knowledge.fragment"
positive c_setup "insert into knowledge.host(host_id,name,repo_locator) values ('one','one','one'),('two','two','two'); insert into knowledge.fragment(host_id,alias,domain,seq,text,kind,source) values ('one','a','d',1,'initial','fact','{\"evidence_refs\":[]}'::jsonb),('one','b','d',2,'neighbor','fact','{\"evidence_refs\":[]}'::jsonb),('two','c','d',1,'other','fact','{\"evidence_refs\":[]}'::jsonb)"
create_actual=$(psql_db -Atc "select count(*)||' op='||min(op)||' revision='||min(h.revision)||' full='||bool_and(snapshot=to_jsonb(f)) from knowledge.fragment_history h join knowledge.fragment f on f.id=h.fragment_id where f.host_id='one' and f.seq=1")
echo "c create expected 1 op=create revision=1 full=true; actual: $create_actual"
[[ "$create_actual" == '1 op=create revision=1 full=true' ]]
positive c_update "update knowledge.fragment set revision=revision+1,text='changed' where host_id='one' and seq=1"
update_actual=$(psql_db -Atc "select count(*)||' snapshot_revisions='||string_agg(snapshot->>'revision',',' order by h.revision)||' ops='||string_agg(op,',' order by h.revision) from knowledge.fragment_history h join knowledge.fragment f on f.id=h.fragment_id where f.host_id='one' and f.seq=1")
echo "c update expected 2 snapshot_revisions=1,1 ops=create,update; actual: $update_actual"
[[ "$update_actual" == '2 snapshot_revisions=1,1 ops=create,update' ]]
negative c_plus2 "update knowledge.fragment set revision=revision+2 where host_id='one' and seq=1"
negative c_same "update knowledge.fragment set text='bad' where host_id='one' and seq=1"
negative c_delete "delete from knowledge.fragment where host_id='one' and seq=1"
positive c_deprecate "begin; select set_config('knowledge.actor','tester',true); update knowledge.fragment set revision=revision+1, active=false where host_id='one' and seq=1; commit"
[[ "$(psql_db -Atc "select op||' '||(snapshot->>'active')||' '||coalesce(actor,'NULL') from knowledge.fragment_history h join knowledge.fragment f on f.id=h.fragment_id where f.host_id='one' and f.seq=1 and h.revision=3")" == 'deprecate true tester' ]]
positive d_setup "insert into knowledge.work_unit(work_unit_id,host_id,partition_key) values ('00000000-0000-0000-0000-000000000001','one','d'); insert into knowledge.work_unit_event(work_unit_id,\"to\",source) values ('00000000-0000-0000-0000-000000000001','active','test'); insert into knowledge.ledger_entry(entry_id,host_id,partition_key,work_unit_id,judgement_id,judgement_version,judgement_body) values ('00000000-0000-0000-0000-000000000002','one','d','00000000-0000-0000-0000-000000000001','j',1,'{\"facts\":[]}'::jsonb); insert into knowledge.ledger_entry_event(entry_id,\"to\",actor) values ('00000000-0000-0000-0000-000000000002','proposed','worker')"
for table in fragment_history ledger_entry_event work_unit_event; do
  key=actor; [[ "$table" == fragment_history ]] && key=snapshot
  [[ "$table" == work_unit_event ]] && key=source
  negative "d $table update" "update knowledge.$table set $key=$key"
  negative "d $table delete" "delete from knowledge.$table"
done
positive e_same "insert into knowledge.relation(host_id,src,dst,type,why) select 'one',a.id,b.id,'supersedes','test' from knowledge.fragment a,knowledge.fragment b where a.host_id='one' and a.seq=1 and b.host_id='one' and b.seq=2"
negative e_cross "insert into knowledge.relation(host_id,src,dst,type,why) select 'one',a.id,b.id,'supersedes','bad' from knowledge.fragment a,knowledge.fragment b where a.host_id='one' and a.seq=1 and b.host_id='two' and b.seq=1"
negative f_bad "insert into knowledge.fragment(host_id,alias,domain,seq,text,kind,source) values ('one','bad','d',3,'bad','fact','{\"evidence_refs\":[{\"type\":\"wrong\",\"locator\":\"x\",\"quote\":\"x\"}]}'::jsonb)"
positive f_good "insert into knowledge.fragment(host_id,alias,domain,seq,text,kind,source) values ('one','good','d',3,'good','fact','{\"evidence_refs\":[{\"type\":\"code_test\",\"locator\":\"x\",\"quote\":\"x\"}]}'::jsonb)"
negative g "insert into knowledge.acceptance_policy(host_id,version,ordinal,rule_id,when_json,then_action) values (null,1,1,'duplicate','{}','hold')"
python3 - <<'PY'
import json, subprocess
from policy import DEFAULT_POLICY
rows = subprocess.check_output(['docker','exec','lh-kdb-test-01','psql','-X','-At','-U','postgres','-d','postgres','-c',
    "select json_build_object('id',rule_id,'when',when_json,'then',then_action)::text from knowledge.acceptance_policy where host_id is null order by ordinal"], text=True)
actual = [json.loads(line) for line in rows.splitlines()]
expected = json.loads(json.dumps(DEFAULT_POLICY))
assert actual == expected, (actual, expected)
print('h PASS expected DEFAULT_POLICY exact JSON; actual exact match 10 rows')
PY
# i: supplements (search_path pinned, updated_at auto, default privileges revoked)
psql_db -Atc "select 'i search_path_pinned='||count(*) filter (where proconfig::text like '%search_path=%')||'/'||count(*) from pg_proc where pronamespace='knowledge'::regnamespace;"
psql_db -Atc "select 'i default_acl_rows='||count(*) from pg_default_acl d join pg_namespace n on n.oid=d.defaclnamespace where n.nspname='knowledge';"
psql_db -Atc "select 'i updated_at_advanced='||bool_and(updated_at > created_at) from knowledge.fragment where revision > 1;"
psql_db -c "create table knowledge.i_probe(x int);" >/dev/null
i_out=$(psql_db -c "set role anon; select * from knowledge.i_probe;" 2>&1 || true)
if grep -q 'permission denied' <<<"$i_out"; then echo "i new_table_anon PASS expected rejection; actual: $i_out"; else echo "i new_table_anon FAIL actual: $i_out"; exit 1; fi
echo 'a-h PASS'
