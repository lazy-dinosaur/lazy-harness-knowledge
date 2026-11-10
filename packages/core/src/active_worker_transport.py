"""Active-role transport composition. Ledger owns attempts; malformed paid output is journaled."""
import math
from active_worker import MODEL, BINDINGS, WorkerFailure, build_request, parse_output
from writer_transport import host_call
from writer_worker import WriterFailure
from safety import save

def make_worker(paid, *, inspect=None, send=None):
    inspect = inspect or (lambda:host_call('inspect',host_module='active_worker_host.mjs'))
    send = send or (lambda request:host_call('complete',request,host_module='active_worker_host.mjs'))
    def call(role, packet):
        metadata=inspect()
        if not metadata.get('ok'): raise WorkerFailure(metadata.get('code','E_WORKER_CONFIG'))
        request=build_request(role,packet,metadata.get('inputBudget'))
        def effect():
            try:
                response=send(request)
                if not response.get('ok'): raise WorkerFailure(response.get('code','E_WORKER_TRANSPORT'))
                cost=response.get('cost')
                if type(cost) not in (int,float) or not math.isfinite(cost) or cost<0: raise WorkerFailure('E_WORKER_COST_UNKNOWN')
                if not isinstance(response.get('text'),str) or not response['text'].strip(): raise WorkerFailure('E_WORKER_COMPLETION')
                return {'text':response['text'],'cost':cost,'role':role,'inputBinding':request['inputBinding'],'sourceBinding':request['sourceBinding']}
            except (WorkerFailure,WriterFailure) as exc:
                save(paid.ledger.path.parent/'errors'/('worker-'+request['inputBinding']+'.json'),
                     {'code':exc.code,'role':role,'inputBinding':request['inputBinding'],'uncertain':True})
                raise
        phase='maintenance' if role in ('describe','merge-luna') else 'generation'
        result=paid.ledger.call(phase,[paid.scope,MODEL,BINDINGS[role],request],.10,effect,lambda r:r['cost'],
                               account='subscription',model='Luna',request={'workerBinding':BINDINGS[role],'inputBinding':request['inputBinding'],'role':role})
        # Parsing is strictly AFTER a done receipt. Auth/timeout/uncertain Stop can never split/replay.
        return parse_output(result['text'],role)
    return call
