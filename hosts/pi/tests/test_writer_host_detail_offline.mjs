// p2-s4-1: a writer completion failure must say why (stop reason, part types, redacted short message), never secrets.
import assert from 'node:assert/strict';
import {completeIsolated, HostFailure} from '../src/writer_host.mjs';

const model = {id: 'gpt-6-luna', provider: 'openai', api: 'openai-responses', reasoning: true,
  cost: {input: 1, output: 1, cacheRead: 0, cacheWrite: 0}, contextWindow: 1000, maxTokens: 100};
const runtime = reply => ({getError: () => null, getPhysicalModel: () => model,
  completeSimple: async (m, ctx, opts) => { opts.transformHeaders({}); return reply; }});
const context = {systemPrompt: 's', messages: [{role: 'user', content: 'u', timestamp: 0}]};
const validate = () => context;
let checks = 0;

await assert.rejects(() => completeIsolated({}, runtime({stopReason: 'error', errorMessage: 'boom sk-PRIVATESECRET123456 Bearer abc.def', content: []}), validate),
  e => e instanceof HostFailure && e.code === 'E_WRITER_COMPLETION' && e.detail.stopReason === 'error' &&
       e.detail.errorMessage.includes('boom') && !JSON.stringify(e.detail).includes('PRIVATESECRET') && !JSON.stringify(e.detail).includes('abc.def')); checks++;
await assert.rejects(() => completeIsolated({}, runtime({stopReason: 'length', content: [{type: 'thinking', thinking: 'x'}]}), validate),
  e => e.code === 'E_WRITER_COMPLETION' && e.detail.stopReason === 'length' && e.detail.parts.join() === 'thinking' && e.detail.errorMessage === null); checks++;
await assert.rejects(() => completeIsolated({}, runtime({stopReason: 'stop', content: [{type: 'toolCall'}]}), validate),
  e => e.code === 'E_WRITER_COMPLETION' && e.detail.parts.join() === 'toolCall'); checks++;
console.log(JSON.stringify({ok: true, checks}));
