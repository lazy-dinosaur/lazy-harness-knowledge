// Active-role host. Reuse the writer's installed ModelRuntime/serializer, never an agent.
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {HostFailure, completeIsolated, inspectRuntime, loadRuntime} from './writer_host.mjs';
const fail = code => {throw new HostFailure(code);};
const roles = new Set(['extract','reextract','domain-name','subject-name','describe','merge-luna','group-name']);
const canonical = value => JSON.stringify(value && typeof value === 'object' ? (Array.isArray(value) ? value.map(v=>JSON.parse(canonical(v))) : Object.fromEntries(Object.keys(value).sort().map(k=>[k,JSON.parse(canonical(value[k]))]))) : value);
const digest = value => createHash('sha256').update(canonical(value)).digest('hex');
const keys = (v, expected) => v && Object.keys(v).sort().join(',') === expected.split(',').sort().join(',');
export function validateRoleRequest(request, budget) {
  if (!keys(request,'schemaVersion,role,binding,sourceBinding,inputBinding,context') || request.schemaVersion !== 'active-worker/1' || !roles.has(request.role)) fail('E_WORKER_INPUT');
  const ctx=request.context;
  if (!keys(ctx,'systemPrompt,messages') || typeof ctx.systemPrompt !== 'string' || !Array.isArray(ctx.messages) || ctx.messages.length!==1) fail('E_WORKER_INPUT');
  const message=ctx.messages[0];
  if (!keys(message,'role,content,timestamp') || message.role!=='user' || message.timestamp!==0 || typeof message.content!=='string') fail('E_WORKER_INPUT');
  let env;
  try {env=JSON.parse(message.content);} catch {fail('E_WORKER_INPUT');}
  if (!keys(env,'schemaVersion,promptVersion,role,sourceBinding,selectionBinding,data,inputBinding') || env.schemaVersion!==request.schemaVersion || env.promptVersion!=='active-worker-prompt/12' || env.role!==request.role) fail('E_WORKER_BINDING');
  if (![env.sourceBinding,env.selectionBinding,env.inputBinding,request.binding].every(v=>typeof v==='string' && /^[a-f0-9]{64}$/.test(v))) fail('E_WORKER_BINDING');
  const binding=digest([env.schemaVersion,env.promptVersion,'openai/gpt-6-luna:medium',env.role,ctx.systemPrompt]);
  const {inputBinding,...unsigned}=env;
  if (binding!==request.binding || env.sourceBinding!==request.sourceBinding || digest(env.data)!==env.sourceBinding || digest([binding,unsigned])!==inputBinding || inputBinding!==request.inputBinding) fail('E_WORKER_BINDING');
  const allowed = {
    extract:'source,source_raw,source_start,chunk_range,context,origin,migration',
    reextract:'source,source_raw,unit,domain,failure_reason',
    'domain-name':'source,source_raw,unit,domain,existing,naming_context',
    'subject-name':'source,source_raw,unit,domain,existing,naming_context',
    describe:'domain,name,subject_type,description,fragments,subjects,domains',
    'merge-luna':'domain,name,subject_type,description,fragments,subjects,domains',
    'group-name':'domains,existing_groups',
  }[request.role].split(',');
  // Task363-N: domain-name may carry OPTIONAL existing area names (language/breadth reference only).
  const optional = request.role==='domain-name' && 'catalog_names' in (env.data||{}) ? ['catalog_names'] : [];
  if (optional.length) {
    const names=env.data.catalog_names;
    if (!Array.isArray(names) || names.length>200 || names.some(n=>typeof n!=='string' || !n.trim() || n.length>80)) fail('E_WORKER_INPUT');
    // #20 / S003 review A: one controller re-ask may carry feedback, only together with catalog_names (same rule as the core).
    for (const key of ['language_feedback','shared_component_feedback']) {
      if (key in env.data) {
        const v=env.data[key];
        if (typeof v!=='string' || !v.trim() || v.length>600) fail('E_WORKER_INPUT');
        optional.push(key);
      }
    }
  }
  if (!env.data || Object.keys(env.data).some(k=>!allowed.includes(k) && !optional.includes(k))) fail('E_WORKER_INPUT');
  if (['domain-name','subject-name'].includes(request.role)) {
    const n=env.data.naming_context;
    if (!keys(env.data,[...allowed,...optional].join(',')) || env.data.existing!==null
        || !keys(n,'contract,scope,stage,existing_match,final_admission')
        || n.contract!=='naming-proposal/1' || n.scope!==request.role.split('-')[0]
        || n.stage!=='candidate-proposal' || n.existing_match!=='unselected'
        || n.final_admission!=='pending') fail('E_WORKER_INPUT');
  }
  if (Buffer.byteLength(ctx.systemPrompt+message.content)+1024>budget) fail('E_WORKER_BOUND');
  return ctx;
}
export async function completeRole(request,runtime,options={}) {
  return completeIsolated(request,runtime,validateRoleRequest,options);
}
async function main() {
  try {
    const mode=process.argv[3];
    if (!['inspect','complete'].includes(mode)) fail('E_WORKER_ROLE');
    const prepared=inspectRuntime(await loadRuntime(process.argv[2],true));
    if (mode==='inspect') return {ok:true,inputBudget:prepared.inputBudget};
    let raw=''; for await (const chunk of process.stdin) raw+=chunk;
    const request=JSON.parse(raw); validateRoleRequest(request,prepared.inputBudget);
    return await completeRole(request,await loadRuntime(process.argv[2]));
  } catch (error) {return {ok:false,code:error instanceof HostFailure ? error.code : 'E_WORKER_TRANSPORT'};}
}
if (process.argv[1] && resolve(process.argv[1])===fileURLToPath(import.meta.url)) process.stdout.write(JSON.stringify(await main()));
