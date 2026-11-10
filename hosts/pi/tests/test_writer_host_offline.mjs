// Standalone node test. No agent session, real auth, HTTP, embedding, or subprocess.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import tls from 'node:tls';
import http from 'node:http';
import https from 'node:https';
import child from 'node:child_process';
import {pathToFileURL} from 'node:url';

const forbidden = () => { throw new Error('offline side effect forbidden'); };
const root = path.resolve(process.argv[2]);
const home = path.join(root, 'synthetic-home'); fs.mkdirSync(home,{recursive:true});
process.env.HOME = home; process.env.PI_CODING_AGENT_DIR = home; process.env.PI_OFFLINE='1';
for (const key of Object.keys(process.env)) if (/KEY|TOKEN|SECRET|DSN|CREDENTIAL/.test(key)) delete process.env[key];
globalThis.fetch=forbidden;
net.connect=net.createConnection=tls.connect=http.request=https.request=forbidden;
net.Socket.prototype.connect=forbidden;
child.spawn=child.exec=child.execFile=child.fork=child.spawnSync=child.execSync=child.execFileSync=forbidden;
// No real file-backed credentials may be read even if a test mistakenly requests them.
const readSync=fs.readFileSync, readAsync=fs.promises.readFile;
fs.readFileSync=function(file,...args) { if (String(file).endsWith('auth.json')) forbidden(); return readSync.call(this,file,...args); };
fs.promises.readFile=async function(file,...args) { if (String(file).endsWith('auth.json')) forbidden(); return readAsync.call(this,file,...args); };
const {syncBuiltinESMExports}=await import('node:module'); syncBuiltinESMExports();
const {ModelRuntime}=await import(pathToFileURL(path.resolve(process.argv[3])).href);
const {completeWriter,inspectRuntime,loadRuntime,HostFailure}=await import('../src/writer_host.mjs');
const captures=JSON.parse(fs.readFileSync(path.join(root,'caller-capture.json'),'utf8'));
let authReads=0;
const credentials={read:async provider=>{assert.equal(provider,'openai'); authReads++; return {type:'api_key',key:'FAKE_HOST_ONLY_KEY'};},
  list:async()=>[],modify:forbidden,delete:forbidden};
const runtime=await ModelRuntime.create({credentials,modelsPath:null,allowModelNetwork:false,refreshOnCreate:false});
assert.equal(authReads,0);
assert.ok(inspectRuntime(runtime).inputBudget>0);
await loadRuntime(process.argv[3],true); // Synthetic models/home and rejecting credentials; inspection only.
assert.equal(authReads,0);
let checks=0, wireCount=0;
function sse(text,usage=true) {
  const output={id:'msg_fake',type:'message',role:'assistant',status:'completed',content:[{type:'output_text',text,annotations:[]}]};
  const response={id:'resp_fake',object:'response',status:'completed',model:'gpt-6-luna',output:[output],
    ...(usage?{usage:{input_tokens:100,output_tokens:30,total_tokens:130,input_tokens_details:{cached_tokens:0},output_tokens_details:{reasoning_tokens:0}}}:{})};
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
  const result=await completeWriter(capture.request,runtime,{fetch:async(url,init)=>{
    fetchCalls++; const wire=JSON.parse(init.body); wires.push(wire);
    assert.ok(String(url).includes('/responses'));
    assert.ok(!JSON.stringify(wire).includes('FAKE_HOST_ONLY_KEY'));
    assert.ok(new Headers(init.headers).get('authorization')?.includes('FAKE_HOST_ONLY_KEY'));
    return sse(JSON.stringify(capture.reply));
  },onWire:wire=>{wireCount++; assert.equal(wire.model,'gpt-6-luna');assert.equal(wire.reasoning.effort,'medium');
    assert.equal(wire.tools,undefined);assert.equal(wire.input.length,2);
    assert.ok(!JSON.stringify(wire).includes('PARENT_TRANSCRIPT'));}});
  assert.equal(fetchCalls,1); assert.equal(result.ok,true); assert.ok(Number.isFinite(result.cost));
  assert.deepEqual(JSON.parse(result.text),capture.reply); checks++;
}
// Preserve literal harness language as user evidence, not automatic system/bootstrap context.
const q=structuredClone(captures[0].request); const env=JSON.parse(q.context.messages[0].content);
env.data.source_raw+='\nAGENTS source-only harness phrase'; env.data.source+='\nAGENTS source-only harness phrase';
q.context.messages[0].content=JSON.stringify(env);
await completeWriter(q,runtime,{fetch:async(_url,init)=>{const wire=JSON.parse(init.body);
  assert.ok(JSON.stringify(wire.input[1]).includes('AGENTS source-only harness phrase'));
  assert.ok(!JSON.stringify(wire.input[0]).includes('AGENTS source-only harness phrase'));
  return sse(JSON.stringify(captures[0].reply));}}); checks++;
await assert.rejects(()=>completeWriter(captures[0].request,runtime,{fetch:async()=>sse('{}',false)}),e=>e instanceof HostFailure && e.code==='E_WRITER_COST_UNKNOWN'); checks++;
await assert.rejects(()=>completeWriter(captures[0].request,runtime,{fetch:async()=>{throw new Error('timeout');}}),e=>e instanceof HostFailure && e.code==='E_WRITER_COMPLETION'); checks++;
const unknown=structuredClone(captures[0].request); unknown.context.tools=[{name:'read'}];
await assert.rejects(()=>completeWriter(unknown,runtime,{fetch:forbidden}),e=>e.code==='E_WRITER_INPUT');checks++;
assert.throws(()=>inspectRuntime({getError:()=>null,getPhysicalModel:()=>undefined}),e=>e.code==='E_WRITER_UNSUPPORTED');checks++;
// Config cannot smuggle previous conversations or hidden fields past the real serializer.
const base=runtime.getPhysicalModel('openai','gpt-6-luna');
const injectedRuntime={getError:()=>null,getPhysicalModel:()=>({...base,samplingParams:{previous_response_id:'PARENT_FORGED'}}),
  completeSimple:runtime.completeSimple.bind(runtime)};
await assert.rejects(()=>completeWriter(captures[0].request,injectedRuntime,{fetch:forbidden}),e=>e.code==='E_WRITER_COMPLETION');checks++;

const noAuth=await ModelRuntime.create({credentials:{...credentials,read:async()=>undefined},modelsPath:null,allowModelNetwork:false,refreshOnCreate:false});
await assert.rejects(()=>completeWriter(captures[0].request,noAuth,{fetch:forbidden}),e=>e.code==='E_WRITER_AUTH');checks++;
const badAuth=await ModelRuntime.create({credentials:{...credentials,read:async()=>{throw new Error('PRIVATE_TOKEN_NOT_FOR_LOG');}},modelsPath:null,allowModelNetwork:false,refreshOnCreate:false});
await assert.rejects(()=>completeWriter(captures[0].request,badAuth,{fetch:forbidden}),e=>e.code==='E_WRITER_AUTH' && !e.message.includes('PRIVATE_TOKEN_NOT_FOR_LOG'));checks++;
assert.throws(()=>inspectRuntime({getError:()=> 'private malformed config'}),e=>e.code==='E_WRITER_CONFIG');checks++;

assert.equal(wireCount,4);assert.ok(authReads>=4);
fs.writeFileSync(path.join(root,'wire-capture.json'),JSON.stringify(wires,null,2));
console.log(JSON.stringify({ok:true,checks,callerWires:wireCount,auth:'synthetic-only',network:0,agentSessions:0}));
