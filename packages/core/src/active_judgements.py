"""Active typed Jev projections; preserve HTTP/model/questions/templates, no Luna substitution."""
import json
from urllib.request import Request
import host_defaults
from active_worker import project, UNIT, IDENTITY, WorkerFailure
from writer_worker import digest

SCHEMA_VERSION='active-jev/1'
PROMPT_VERSION='active-jev-criteria/1'
CANDIDATE={**IDENTITY,'key':str,'rank':int,'similarity':float,'area':str,'example':str}
FIELDS={'fact':str,'keywords':[str],'source_ranges':[[int]],'reason':str,'why':str}
STATE={'unit':UNIT,'domain':IDENTITY,'candidate':CANDIDATE,'proposed':CANDIDATE,'candidates':[CANDIDATE],
       'sentence':str,'fields':FIELDS,'grounding_version':int,'source':str,'source_start_line':int,
       'written_kind':str,'modality_version':str,
       'source_structure':{'selected':[{'start':int,'end':int,'quote':str}],
                           'structural_cues':[{'line':int,'quote':str}],'scope':str}}
ACTOR={'narrative':str,'candidate_fact':str,'evidence_quote':str,'existing_records_excerpt':str,
       'target_excerpt':str,'deprecate_reason':str}
CHECK={'sentence':str,'excluded_names':[str],'candidate_names':[str]}

def _schema(value):
    if isinstance(value,dict): return {k:_schema(v) for k,v in value.items()}
    if isinstance(value,list): return [_schema(value[0])]
    return value.__name__

BINDING=digest([SCHEMA_VERSION,PROMPT_VERSION,_schema(STATE),_schema(ACTOR),_schema(CHECK),
                'closed projections; existing calibrated questions unchanged'])

def state_projection(role,state):
    if role in ('selection','placement','grounding'):
        out=project(state,STATE)
        if 'checks' in state:
            if not isinstance(state['checks'],dict): raise WorkerFailure('E_JEV_INPUT')
            out['checks']={str(k):project(v,CHECK) for k,v in state['checks'].items()}
        return out
    if role=='maintenance-identity':
        comparison=state.get('comparison',{})
        return {'comparison':{kind:[project(v,{'name':str,'type':str,'description':str,'parent':str}) for v in comparison[kind]]
                              for kind in ('subjects','domains') if kind in comparison}}
    if role=='worktime-actor': return project(state,ACTOR)
    raise WorkerFailure('E_JEV_ROLE')

def bind_body(role,body,*,template=None,original=None):
    if not isinstance(body,dict) or set(body)!={'model','state','questions'}: raise WorkerFailure('E_JEV_INPUT')
    state=state_projection(role,body['state'])
    if 'items' in body['state']:
        state['items']=project(body['state']['items'],[{'i':int,'text':str}])
    questions={}
    for key,row in body['questions'].items():
        if not isinstance(row,dict) or not set(row)<= {'type','instructions','criteria'} or row.get('type') not in ('choice','noul','score'): raise WorkerFailure('E_JEV_INPUT')
        if not isinstance(row.get('instructions'),str): raise WorkerFailure('E_JEV_INPUT')
        criteria=row.get('criteria')
        if not isinstance(criteria,(dict,list)) or any(not isinstance(v,str) for v in (criteria.values() if isinstance(criteria,dict) else criteria)): raise WorkerFailure('E_JEV_INPUT')
        questions[key]=dict(row)
    source_binding=digest(state)
    meta={'schemaVersion':SCHEMA_VERSION,'promptVersion':PROMPT_VERSION,'role':role,'binding':BINDING,
          'sourceBinding':source_binding,'selectionBinding':digest(original if original is not None else body),
          'template':template}
    meta['inputBinding']=digest([meta,body['model'],state,questions])
    state['workerContract']=meta
    # Existing typed HTTP state, not a natural-language task or new quality judgement.
    return {'model':body['model'],'state':state,'questions':questions}

def verify_body(body):
    try:
        state=dict(body['state']); meta=dict(state.pop('workerContract'))
        claimed=meta.pop('inputBinding')
        if meta['schemaVersion']!=SCHEMA_VERSION or meta['promptVersion']!=PROMPT_VERSION or meta['binding']!=BINDING:
            raise WorkerFailure('E_JEV_BINDING')
        if meta['sourceBinding']!=digest(state) or claimed!=digest([meta,body['model'],state,body['questions']]):
            raise WorkerFailure('E_JEV_BINDING')
        return body
    except (KeyError,TypeError,ValueError):
        raise WorkerFailure('E_JEV_BINDING') from None


def partition_body(body, max_questions=3):
    """Rebind finite same-state physical sends, never drop independent questions."""
    verify_body(body)
    if type(max_questions) is not int or max_questions<1:
        raise WorkerFailure('E_JEV_INPUT')
    if len(body['questions'])<=max_questions:return [body]
    import copy
    state=copy.deepcopy(body['state']);meta=state.pop('workerContract')
    keys=list(body['questions']);parts=[]
    for start in range(0,len(keys),max_questions):
        selected=keys[start:start+max_questions]
        part={'model':body['model'],'state':copy.deepcopy(state),
              'questions':{key:copy.deepcopy(body['questions'][key]) for key in selected}}
        parts.append(bind_body(meta['role'],part,template=meta['template'],original=body))
    return parts


def wire(role, http=None, *, template=None):
    send=http or host_defaults.lazy_judgement_http
    def bound(request,*args,**kwargs):
        body=bind_body(role,json.loads(request.data),template=template)
        # Authorization stays in the HTTP host headers, never model state.
        final=Request(request.full_url,data=json.dumps(body,ensure_ascii=False,allow_nan=False).encode(),
                      headers=dict(request.header_items()),method=request.get_method())
        return send(final,*args,**kwargs)
    return bound

def make_choice(cfg,*,http=None):
    import capture_audit
    return capture_audit.make_judge(cfg,http=wire('selection',http),text_limit=None)

def make_state_choice(cfg,*,http=None,max_attempts=2):
    """One Choice about named state fields; no synthetic text item or batch indirection."""
    import math
    import capture_audit
    if max_attempts not in (1,2): raise ValueError('max_attempts must be 1 or 2')
    send=wire('selection',http,template='single-state-choice/1')
    def choose(state,instructions,criteria):
        if not isinstance(instructions,str) or not isinstance(criteria,dict) or not criteria:raise WorkerFailure('E_JEV_INPUT')
        body={'model':cfg['jev_model'],'state':state,'questions':{'m0':{'type':'choice','instructions':instructions,'criteria':criteria}}}
        answer=capture_audit.request_answers(cfg,body,http=send,max_attempts=max_attempts)['m0']
        probabilities=answer.get('probabilities')
        if answer.get('type')!='choice' or not isinstance(probabilities,dict) or set(probabilities)!=set(criteria) or answer.get('choice') not in criteria:
            raise WorkerFailure('E_JEV_RESPONSE')
        values=list(probabilities.values())
        if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in values) or not math.isclose(sum(values),1.,abs_tol=1e-6):
            raise WorkerFailure('E_JEV_RESPONSE')
        return answer
    return choose


def make_ask(cfg,role,*,http=None):
    import worker_tools
    return worker_tools.make_ask(cfg,http=wire(role,http))

def make_actor_judge(cfg,*,paid=None,judge=None):
    if judge is None:
        from digest_driver import _jev_judge
        judge=_jev_judge
    def call(packet):
        template={'template_id':packet.get('template_id'),'template_version':packet.get('template_version')}
        captured={}
        def final(body):
            result=bind_body('worktime-actor',body,template=template,original=packet)
            captured.update(result)
            return result
        answer=judge(packet,cfg=cfg,request_projector=final)
        # Never mutate DB packets: cached_digestion_answers compares their exact JSON.
        # Paid HTTP hashes the final body; this separate active receipt links its exact binding.
        contract=captured['state']['workerContract']
        if paid is not None:
            from safety import save
            save(paid.ledger.path.parent/'active-jev-receipts'/(contract['inputBinding']+'.json'),
                 {'contract':contract,'scope':paid.scope,'request_sha':digest(captured),
                  'answers_sha':digest(answer),'model':captured['model']})
        return {**answer,'worker_contract':contract}
    return call
