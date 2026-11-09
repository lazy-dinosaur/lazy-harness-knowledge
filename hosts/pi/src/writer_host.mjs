// Host transport only. Never import createAgentSession, resources, extensions, or tools.
import { pathToFileURL, fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

export const MODEL_ID = 'gpt-6-luna';
export class HostFailure extends Error {
  constructor(code) { super(code); this.code = code; }
}
const fail = code => { throw new HostFailure(code); };
const finite = n => typeof n === 'number' && Number.isFinite(n) && n >= 0;

export function inspectRuntime(runtime) {
  if (runtime.getError()) fail('E_WRITER_CONFIG');
  const model = runtime.getPhysicalModel('openai', MODEL_ID);
  if (!model || model.id !== MODEL_ID || model.provider !== 'openai' || model.api !== 'openai-responses' || !model.reasoning) fail('E_WRITER_UNSUPPORTED');
  if (!model.cost || !['input', 'output', 'cacheRead', 'cacheWrite'].every(k => finite(model.cost[k]))) fail('E_WRITER_CONFIG');
  if (!Number.isInteger(model.contextWindow) || !Number.isInteger(model.maxTokens) || model.contextWindow <= model.maxTokens) fail('E_WRITER_BOUND');
  return {ok: true, model, inputBudget: model.contextWindow - model.maxTokens};
}

export function validateRequest(request, budget) {
  if (!request || request.schemaVersion !== 'unit-writer/1' || !['write', 'rewrite'].includes(request.stage) ||
      Object.keys(request).sort().join(',') !== 'binding,context,inputBinding,schemaVersion,sourceBinding,stage') fail('E_WRITER_INPUT');
  const ctx = request.context;
  if (!ctx || Object.keys(ctx).sort().join(',') !== 'messages,systemPrompt' || typeof ctx.systemPrompt !== 'string' || !ctx.systemPrompt ||
      !Array.isArray(ctx.messages) || ctx.messages.length !== 1) fail('E_WRITER_INPUT');
  const message = ctx.messages[0];
  if (message.role !== 'user' || typeof message.content !== 'string' || Object.keys(message).sort().join(',') !== 'content,role,timestamp') fail('E_WRITER_INPUT');
  if (Buffer.byteLength(ctx.systemPrompt + message.content) + 1024 > budget) fail('E_WRITER_BOUND');
  return ctx;
}

function wireText(message) {
  return typeof message.content === 'string' ? message.content : (message.content || []).map(p => p.text || '').join('');
}

export async function completeWriter(request, runtime, options = {}) {
  return completeIsolated(request, runtime, validateRequest, options);
}

// One SDK/auth/usage/final-payload authority, shared by explicit role contracts.
export async function completeIsolated(request, runtime, validate, {fetch, signal = AbortSignal.timeout(900000), onWire} = {}) {
  const {model, inputBudget} = inspectRuntime(runtime);
  const context = validate(request, inputBudget);
  let authResolved = false;
  let usageSeen = false;
  const message = await runtime.completeSimple(model, context, {
    reasoning: 'medium', signal, maxRetries: 0, fetch,
    // Runs only after the SDK resolves request auth; never resolve credentials twice.
    transformHeaders(headers) { authResolved = true; return headers; },
    onProviderStreamEvent(event) {
      if (event.type === 'response.completed') {
        const usage = event.response?.usage;
        usageSeen = !!usage && finite(usage.input_tokens) && finite(usage.output_tokens) && finite(usage.total_tokens);
      }
    },
    onPayload(payload) {
      // Verify the actual SDK serializer boundary, including config sampling overrides.
      const allowed = new Set(['model','input','stream','prompt_cache_key','prompt_cache_retention','prompt_cache_options','store','max_output_tokens','reasoning','include','temperature','service_tier']);
      const plainMessage = message => Object.keys(message).every(k => ['role','content'].includes(k)) &&
        (typeof message.content === 'string' || (Array.isArray(message.content) && message.content.length === 1 &&
         message.content.every(p => p.type === 'input_text' && Object.keys(p).every(k => ['type','text'].includes(k)))));
      if (Object.keys(payload).some(k => !allowed.has(k)) || payload.store !== false ||
          payload.model !== MODEL_ID || payload.tools?.length || payload.tool_choice || !Array.isArray(payload.input) || payload.input.length !== 2 ||
          !payload.input.every(plainMessage) || !['system', 'developer'].includes(payload.input[0].role) || payload.input[1].role !== 'user' ||
          wireText(payload.input[0]) !== context.systemPrompt || wireText(payload.input[1]) !== context.messages[0].content ||
          payload.reasoning?.effort !== 'medium') fail('E_WRITER_WIRE');
      onWire?.(payload);
    },
  });
  if (!authResolved) fail('E_WRITER_AUTH');
  if (message.stopReason !== 'stop' || message.content?.some(p => !['text', 'thinking'].includes(p.type))) fail('E_WRITER_COMPLETION');
  const usage = message.usage;
  if (!usageSeen || !usage || !['input', 'output', 'cacheRead', 'cacheWrite', 'totalTokens'].every(k => finite(usage[k])) ||
      !usage.cost || !['input', 'output', 'cacheRead', 'cacheWrite', 'total'].every(k => finite(usage.cost[k]))) fail('E_WRITER_COST_UNKNOWN');
  const text = message.content.filter(p => p.type === 'text').map(p => p.text).join('\n');
  if (!text.trim()) fail('E_WRITER_COMPLETION');
  return {ok: true, text, cost: usage.cost.total};
}

export async function loadRuntime(path, inspectOnly = false) {
  const {ModelRuntime} = await import(pathToFileURL(resolve(path)).href);
  // Inspection/prepare must not construct file-backed AuthStorage or resolve any key.
  const credentials = inspectOnly ? {
    read: async () => { throw new HostFailure('E_WRITER_AUTH_IN_PREPARE'); },
    list: async () => [], modify: async () => { throw new HostFailure('E_WRITER_AUTH_IN_PREPARE'); }, delete: async () => {},
  } : undefined;
  return ModelRuntime.create({credentials, allowModelNetwork: false, refreshOnCreate: false});
}

async function main() {
  try {
    const mode = process.argv[3];
    if (!['inspect', 'complete'].includes(mode)) fail('E_WRITER_STAGE');
    // Both modes validate exact binding before loading real host credentials.
    const prepared = inspectRuntime(await loadRuntime(process.argv[2], true));
    if (mode === 'inspect') return {ok: true, inputBudget: prepared.inputBudget};
    let raw = '';
    for await (const chunk of process.stdin) raw += chunk;
    const request = JSON.parse(raw);
    validateRequest(request, prepared.inputBudget);
    return await completeWriter(request, await loadRuntime(process.argv[2]));
  } catch (error) {
    // Never serialize SDK auth errors, headers, credential commands, or secret config.
    return {ok: false, code: error instanceof HostFailure ? error.code : 'E_WRITER_TRANSPORT'};
  }
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.stdout.write(JSON.stringify(await main()));
}
