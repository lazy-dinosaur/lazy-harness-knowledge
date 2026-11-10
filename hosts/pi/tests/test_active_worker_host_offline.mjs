// Standalone installed-SDK serializer test. Explicit capture root + runtime entry; no discovery/auth/network.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import tls from 'node:tls';
import http from 'node:http';
import https from 'node:https';
import child from 'node:child_process';
import {pathToFileURL} from 'node:url';
const forbidden=()=>{throw new Error('offline side effect forbidden');};
const root=path.resolve(process.argv[2]);
const home=path.join(root,'synthetic-active-home'); fs.mkdirSync(home,{recursive:true});
process.env.HOME=home; process.env.PI_CODING_AGENT_DIR=home; process.env.PI_OFFLINE='1';
for (const key of Object.keys(process.env)) if (/KEY|TOKEN|SECRET|DSN|CREDENTIAL/.test(key)) delete process.env[key];
globalThis.fetch=forbidden;
net.connect=net.createConnection=tls.connect=http.request=https.request=forbidden;
net.Socket.prototype.connect=forbidden;
child.spawn=child.exec=child.execFile=child.fork=child.spawnSync=child.execSync=child.execFileSync=forbidden;
const readSync=fs.readFileSync,readAsync=fs.promises.readFile;
fs.readFileSync=function(file,...args) {if (String(file).endsWith('auth.json')) forbidden();return readSync.call(this,file,...args);};
fs.promises.readFile=async function(file,...args) {if (String(file).endsWith('auth.json')) forbidden();return readAsync.call(this,file,...args);};
const {syncBuiltinESMExports}=await import('node:module'); syncBuiltinESMExports();
// Guards are installed before any application or SDK import.
const {ModelRuntime}=await import(pathToFileURL(path.resolve(process.argv[3])).href);
const {completeRole,validateRoleRequest}=await import('../src/active_worker_host.mjs');
const {completeWriter}=await import('../src/writer_host.mjs');
const captures=JSON.parse(fs.readFileSync(path.join(root,'active-caller-capture.json'),'utf8'));
assert.equal(captures.length,6);
let authReads=0,checks=0,wireCount=0;
const credentials={read:async provider=>{assert.equal(provider,'openai');authReads++;return {type:'api_key',key:'FAKE_HOST_ONLY_KEY'};},list:async()=>[],modify:forbidden,delete:forbidden};
const runtime=await ModelRuntime.create({credentials,modelsPath:null,allowModelNetwork:false,refreshOnCreate:false});
assert.equal(authReads,0);
function sse(text,usage=true) {
  const output={id:'msg_fake',type:'message',role:'assistant',status:'completed',content:[{type:'output_text',text,annotations:[]}]};
  const response={id:'resp_fake',object:'response',status:'completed',model:'gpt-6-luna',output:[output],...(usage?{usage:{input_tokens:100,output_tokens:30,total_tokens:130,input_tokens_details:{cached_tokens:0},output_tokens_details:{reasoning_tokens:0}}}:{})};
  const events=[{type:'response.created',response:{...response,status:'in_progress',output:[]}},
    {type:'response.output_item.added',output_index:0,item:{...output,status:'in_progress',content:[]}},
    {type:'response.content_part.added',item_id:output.id,output_index:0,content_index:0,part:{type:'output_text',text:'',annotations:[]}},
    {type:'response.output_text.delta',item_id:output.id,output_index:0,content_index:0,delta:text},
    {type:'response.output_text.done',item_id:output.id,output_index:0,content_index:0,text},
    {type:'response.output_item.done',output_index:0,item:output},{type:'response.completed',response}];
  return new Response(events.map(e=>`event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`).join(''),{headers:{'Content-Type':'text/event-stream'}});
}
const wires=[];
for (const capture of captures) {
  let fetchCalls=0;
  const result=await completeRole(capture.request,runtime,{fetch:async(url,init)=>{
    fetchCalls++;const wire=JSON.parse(init.body);wires.push(wire);
    assert.ok(String(url).includes('/responses'));
    assert.ok(new Headers(init.headers).get('authorization')?.includes('FAKE_HOST_ONLY_KEY'));
    assert.ok(!JSON.stringify(wire).includes('FAKE_HOST_ONLY_KEY'));
    return sse(JSON.stringify(capture.reply));
  },onWire:wire=>{
    wireCount++;assert.equal(wire.model,'gpt-6-luna');assert.equal(wire.reasoning.effort,'medium');
    assert.equal(wire.store,false);assert.equal(wire.tools,undefined);assert.equal(wire.input.length,2);
    for (const denied of ['PARENT_TRANSCRIPT','PRIVATE_DSN','PRIVATE_HOST','PRIVATE_UUID']) assert.ok(!JSON.stringify(wire).includes(denied));
    assert.ok(!JSON.stringify(wire.input[0]).includes('RAW_EVIDENCE_SENTINEL'));
    assert.ok(JSON.stringify(wire.input[1]).includes('RAW_EVIDENCE_SENTINEL'));
    if (['domain-name','subject-name'].includes(capture.request.role)) {
      assert.ok(JSON.stringify(wire.input[1]).includes('naming-proposal/1'));
      assert.ok(JSON.stringify(wire.input[1]).includes('final_admission'));
    }
  }});
  assert.equal(fetchCalls,1);assert.equal(result.ok,true);assert.ok(Number.isFinite(result.cost));
  assert.deepEqual(JSON.parse(result.text),capture.reply);checks++;
  for (const change of [q=>q.binding='forged',q=>q.inputBinding='0'.repeat(64),q=>q.role='describe',
                         q=>q.context.messages[0].content=q.context.messages[0].content.replace('RAW_EVIDENCE_SENTINEL','STALE_SOURCE'),
                         q=>q.context.tools=[{name:'read'}],q=>q.context.messages.push({role:'assistant',content:'PARENT_TRANSCRIPT'})]) {
    const bad=structuredClone(capture.request);change(bad);
    // role=describe is unchanged on one iteration; choose an always-different role instead.
    if (JSON.stringify(bad)===JSON.stringify(capture.request)) bad.role='extract';
    assert.throws(()=>validateRoleRequest(bad,1000000));checks++;
  }
  assert.throws(()=>validateRoleRequest(capture.request,1),e=>e.code==='E_WORKER_BOUND');checks++;
}
// Re-sign mutated DTOs: failures must be semantic shape/version guards, not only stale hashes.
const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
const digest=v=>createHash('sha256').update(JSON.stringify(canonical(v))).digest('hex');
function changedRequest(original,change) {
  const q=structuredClone(original),env=JSON.parse(q.context.messages[0].content);change(env);
  env.sourceBinding=digest(env.data);q.sourceBinding=env.sourceBinding;
  q.binding=digest([env.schemaVersion,env.promptVersion,'openai/gpt-6-luna:medium',env.role,q.context.systemPrompt]);
  const {inputBinding,...unsigned}=env;env.inputBinding=digest([q.binding,unsigned]);q.inputBinding=env.inputBinding;
  q.context.messages[0].content=JSON.stringify(env);return q;
}
for (const capture of captures.filter(c=>['domain-name','subject-name'].includes(c.request.role))) {
  const env=JSON.parse(capture.request.context.messages[0].content);
  assert.equal(env.promptVersion,'active-worker-prompt/9');
  assert.equal(env.data.naming_context.final_admission,'pending');checks++;
  for (const change of [e=>delete e.data.naming_context,
      e=>e.data.naming_context.scope=e.role==='domain-name'?'subject':'domain',
      e=>e.data.naming_context.stage='final-creation',e=>e.data.naming_context.existing_match='none-confirmed',
      e=>e.data.naming_context.final_admission='allowed',e=>e.data.naming_context.contract='unknown',
      e=>e.data.naming_context.extra='authority',e=>e.data.naming_context=[],e=>e.data.naming_context=null,
      e=>e.data.existing={name:'existing'},e=>delete e.data.existing,e=>e.data.extra='authority',
      e=>delete e.data.source_raw]) {
    assert.throws(()=>validateRoleRequest(changedRequest(capture.request,change),1000000),e=>e.code==='E_WORKER_INPUT');checks++;
  }
  assert.throws(()=>validateRoleRequest(changedRequest(capture.request,e=>e.promptVersion='active-worker-prompt/1'),1000000),e=>e.code==='E_WORKER_BINDING');checks++;
}
assert.equal(wireCount,6);
const writerCaptures=JSON.parse(fs.readFileSync(path.join(root,'active-writer-capture.json'),'utf8'));
assert.equal(writerCaptures.length,2);
for (const capture of writerCaptures) {
  const result=await completeWriter(capture.request,runtime,{fetch:async(_url,init)=>{
    const wire=JSON.parse(init.body);assert.equal(wire.input.length,2);assert.equal(wire.tools,undefined);
    return sse(JSON.stringify(capture.reply));
  }});
  assert.deepEqual(JSON.parse(result.text),capture.reply);checks++;
}
await assert.rejects(()=>completeRole(captures[0].request,runtime,{fetch:async()=>sse('{}',false)}),e=>e.code==='E_WRITER_COST_UNKNOWN');checks++;
let attempts=0;
await assert.rejects(()=>completeRole(captures[0].request,runtime,{fetch:async()=>{attempts++;throw new Error('timeout');}}),e=>e.code==='E_WRITER_COMPLETION');
assert.equal(attempts,1);checks++;
const noAuth=await ModelRuntime.create({credentials:{...credentials,read:async()=>undefined},modelsPath:null,allowModelNetwork:false,refreshOnCreate:false});
await assert.rejects(()=>completeRole(captures[0].request,noAuth,{fetch:forbidden}),e=>e.code==='E_WRITER_AUTH');checks++;
// Shared core must not make writer accept the new role schema.
await assert.rejects(()=>completeWriter(captures[0].request,runtime,{fetch:forbidden}),e=>e.code==='E_WRITER_INPUT');checks++;
fs.writeFileSync(path.join(root,'active-wire-capture.json'),JSON.stringify(wires,null,2));
console.log(JSON.stringify({ok:true,checks,callerWires:wireCount,authReads,auth:'synthetic-only',network:0,agentSessions:0}));
