"""Provider-neutral isolated role bindings. No import-time IO or effect authority."""
import copy
import hashlib
import hmac
import json
import secrets
from writer_worker import digest

_KEY = secrets.token_bytes(32)

class Receipt(dict):
    """Process-local validated evidence; serialization deliberately loses authority."""
    pass

def seal(value):
    result = Receipt(copy.deepcopy(value))
    result['_seal'] = hmac.new(_KEY, digest(value).encode(), hashlib.sha256).hexdigest()
    return result

def verified(value):
    from durable_role_review import DurableReceipt
    if type(value) is DurableReceipt:return value.verified()
    return (type(value) is Receipt and isinstance(value.get('_seal'), str)
            and hmac.compare_digest(value['_seal'], hmac.new(_KEY, digest({k:v for k,v in value.items() if k!='_seal'}).encode(), hashlib.sha256).hexdigest()))

def configure_durability(policy):
    store=getattr(policy,'review_store',None)
    if store is not None:
        from durable_role_review import DurableReviewStore
        if type(store) is not DurableReviewStore:raise ValueError('explicit HOST durable store capability required')
        object.__setattr__(policy,'_reviews',store.scope(policy.host_id))

def policy_check(policy, expected, host):
    if (type(policy) is not expected or policy.host_id != host or not all(isinstance(v,str) and v for v in (policy.host_id,policy.provider,policy.model)) or not callable(policy.complete)):
        raise ValueError('explicit trusted role policy required; READ/WRITE/DOMAIN/ADMISSION are separate')

def request(role, schema, prompt, system, policy, data):
    binding = digest([schema,prompt,policy.provider,policy.model,role,system])
    env = {'schemaVersion':schema,'promptVersion':prompt,'role':role,'sourceBinding':digest(data),'data':copy.deepcopy(data)}
    env['inputBinding'] = digest([binding,env])
    user = json.dumps(env,ensure_ascii=False,sort_keys=True,allow_nan=False)
    if len((system+user).encode())+1024 > 144000:
        raise ValueError('full source exceeds isolated role budget; no truncation')
    return {'schemaVersion':schema,'role':role,'binding':binding,'sourceBinding':env['sourceBinding'],'inputBinding':env['inputBinding'],
            'context':{'systemPrompt':system,'messages':[{'role':'user','content':user,'timestamp':0}]}}

def strict_json(raw):
    def pairs(rows):
        d={}
        for k,v in rows:
            if k in d:raise ValueError('duplicate role JSON key')
            d[k]=v
        return d
    def invalid(_):raise ValueError('nonfinite role JSON')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid)

def complete(policy, req):
    response=policy.complete(copy.deepcopy(req))
    scope=getattr(policy,'_reviews',None)
    if scope is not None:scope.record(response)
    p=response.get('provenance',{}) if isinstance(response,dict) else {}
    if (not isinstance(response,dict) or response.get('ok') is not True or not isinstance(p,dict)
        or any(p.get(k)!=v for k,v in {'provider':policy.provider,'model':policy.model,'role':req['role'],'input_binding':req['inputBinding'],'source_binding':req['sourceBinding']}.items())
        or not p.get('response_id')):
        raise ValueError('selected SDK role unavailable or provenance mismatch; no fallback')
    return strict_json(response['text']),copy.deepcopy(p)

def anchors(source, ranges):
    lines=source.splitlines(); result=[]
    if not isinstance(ranges,list) or not ranges:raise ValueError('source ranges required')
    for a,z in ranges:
        if type(a) is not int or type(z) is not int or not 1<=a<=z<=len(lines):raise ValueError('invalid source range')
        result.append({'start':a,'end':z,'quote':'\n'.join(lines[a-1:z])})
    return result

def check_anchors(source, values, required=()):
    if not isinstance(values,list) or not values:raise ValueError('exact source anchors required')
    keys=set()
    for v in values:
        if not isinstance(v,dict) or set(v)!={'start','end','quote'} or anchors(source,[[v['start'],v['end']]])[0]!=v:
            raise ValueError('source quote not bound')
        key=(v['start'],v['end'])
        if key in keys:raise ValueError('duplicate source anchor')
        keys.add(key)
    if not set(map(tuple,required))<=keys:raise ValueError('selected source coverage missing')
    return copy.deepcopy(values)


class ReviewScope:
    """Private process-local anti-shopping journal, never serialized admission authority."""
    def __init__(self):
        import threading
        self._lock = threading.RLock()
        self._entries = {}
        self._active = None

    def retain_hold(self, policy, req, semantic, reason):
        """Trusted host can retain historical negatives, NEVER import positive authority."""
        if not isinstance(reason,str) or not reason.strip():raise ValueError('historical HOLD reason required')
        key = digest([policy.host_id, policy.provider, policy.model, req['role'], semantic])
        with self._lock:
            self._entries[key] = {'request':copy.deepcopy(req),'error':'historical conservative HOLD: '+reason}

    def record(self, response):
        if self._active is not None:
            self._active['raw_response'] = copy.deepcopy(response)

    def review(self, policy, req, semantic, evaluate):
        key = digest([policy.host_id, policy.provider, policy.model, req['role'], semantic])
        with self._lock:
            if key in self._entries:
                prior = self._entries[key]
                if 'error' in prior:
                    raise ValueError('prior semantic review HOLD (no retry): ' + prior['error'])
                if prior['request'] != req:
                    raise ValueError('prior semantic review retained across protocol/identity changes; no fresh authority')
                return copy.deepcopy(prior['receipt'])
            entry = {'request': copy.deepcopy(req)}
            self._entries[key] = entry
            self._active = entry
            try:
                entry['receipt'] = evaluate()
                return copy.deepcopy(entry['receipt'])
            except BaseException as exc:
                entry['error'] = type(exc).__name__ + ': ' + str(exc)
                raise
            finally:
                self._active = None


def semantic_unit(unit):
    """Meaning-bearing inputs only; transport versions and generated IDs are not meaning."""
    keys = ('content', 'target_expression', 'source_ranges', 'source_owner', 'knowledge_form')
    result = {k: copy.deepcopy(unit[k]) for k in keys if k in unit}
    if 'atomic_child' in unit:
        child = unit['atomic_child']
        result['atomic_child'] = {k: copy.deepcopy(child.get(k)) for k in ('claim', 'conditions')}
        result['atomic_child']['parent'] = semantic_unit(child['parent_hypothesis'])
    if unit.get('migration_resolution'):
        # Task371: HOST-verified document/code evidence is new meaning-bearing input, so a re-application is a
        # fresh review question (never a retained prior HOLD), while units without it keep their old meaning.
        result['migration_resolution'] = copy.deepcopy(unit['migration_resolution'])
    return result
