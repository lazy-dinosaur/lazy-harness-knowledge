"""Approved G16 pilot controller. No model text is executed as shell source."""
import os
import re
from pathlib import Path
import subprocess
import sys
import time
from safety import STATE_HOME as HERE, ROOT, Ledger, digest, load, save, exclusive

HOME = HERE / 'pilot-area-10'
PHASES = ('judge','place','write','ground','embed-precompute','absorb','report')


def invocation(phase, sha='', extra=()):
    args = ['pipeline.py',phase,'--scope','pilot-area-10','--approve-plan-sha',sha,*extra]
    return [sys.executable,'-B','-c','import sys; sys.argv='+repr(args)+'; import pipeline; pipeline.main()']


def recover(home, output, retries):
    plan=load(home/'plan.json')
    ledger=Ledger(home/'state/paid.jsonl',digest(plan),plan['caps'])
    latest={r['id']:r for r in ledger.rows}
    pending=[r for r in latest.values() if r['state']=='reserved']
    if len(pending)!=1: raise RuntimeError('not one retained reservation; inspect log')
    row=pending[0]; ident=row['id']
    if retries>=5 or any(r['id']==ident and r['state']=='released' for r in ledger.rows):
        raise RuntimeError('retry limit or same reservation failed twice: '+ident)
    ep=home/'state/errors'/(ident+'.json')
    status=(load(ep) if ep.exists() else {}).get('status')
    transient=(status in (429,499) or isinstance(status,int) and 500<=status<=599 or
               status is None and any(s in output for s in ('URLError','ConnectionError','ConnectError','TimeoutError','ReadTimeout','RemoteProtocolError','ConnectionResetError')))
    if row.get('account','cash')!='cash' or not transient:
        raise RuntimeError('non-transient/subscription/4xx; no replay: '+ident)
    return ident


def recover_logged_failure(output):
    """Outer controller: only the retained no-replay ID with explicit transient HTTP evidence."""
    matches=re.findall(r'no replay: ([a-f0-9]{64})',output)
    if not matches: raise RuntimeError('outer retry requires no-replay evidence')
    receipt=HOME/'state/pilot-run.json'; prior=load(receipt)
    ident=recover(HOME,output,prior['retries'])
    if ident!=matches[-1]: raise RuntimeError('no-replay ID does not match retained reservation')
    status=load(HOME/'state/errors'/(ident+'.json')).get('status')
    if status not in (429,499) and not (isinstance(status,int) and 500<=status<=599):
        raise RuntimeError('outer retry requires explicit transient HTTP status')
    plan=load(HOME/'plan.json')
    subprocess.run(invocation('resolve-reservation',digest(plan),('--reservation-id',ident,
        '--reason','approved outer transient retry; full reservation spent','--count-as-spent')),
        cwd=HERE,check=True,timeout=60)
    prior['retries']+=1;save(receipt,prior)
    print('OUTER RETRY reservation='+ident+' retries='+str(prior['retries']),flush=True)


def main():
    if Path.cwd().resolve()!=ROOT: raise RuntimeError('launch from approved worktree root')
    expected={'LH_EMBED_MODEL':'perplexity/pplx-embed-v1-0.6b','LH_CANDIDATE_TOP_K':'5',
              'LH_EMBED_TIMEOUT':'30','LH_EMBED_HEDGE_AFTER':'10','LH_EMBED_BATCH_SIZE':'64','LH_EMBED_PARALLEL':'1'}
    if any(os.environ.get(k)!=v for k,v in expected.items()): raise RuntimeError('pilot environment mismatch')
    resume='--resume' in sys.argv
    started=time.time(); archive=None
    if not resume:
        with exclusive(HOME):
            if (HOME/'state').exists():
                archive=HOME/('state-before-numbered-domain-'+time.strftime('%Y%m%d%H%M%S'))
                (HOME/'state').rename(archive)
        subprocess.run(invocation('prepare'),cwd=HERE,check=True,timeout=120)
    plan=load(HOME/'plan.json'); sha=digest(plan)
    if plan['cash_caps']['total_usd']!=.5: raise RuntimeError('cash cap is not $0.50')
    print('PILOT plan='+sha+' archive='+str(archive),flush=True)
    subprocess.run(invocation('init-write',sha),cwd=HERE,check=True,timeout=120)
    receipt=HOME/'state/pilot-run.json'
    prior=load(receipt) if resume and receipt.exists() else {'started':started,'archive':str(archive),'plan_sha':sha,'completed':[],'retries':0,'prior_spent_usd':plan['cash_caps'].get('prior_spent_usd',0),'prior_archive':os.environ.get('LH_PILOT_PRIOR_ARCHIVE')}
    save(receipt,prior)
    deadline=time.monotonic()+5*3600
    for phase in PHASES:
        if phase in prior['completed']: continue
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0: raise RuntimeError('finite pilot deadline reached')
            log=HOME/'state'/('run-'+phase+'.log')
            print('PHASE '+phase+' starting',flush=True)
            with log.open('a') as stream:
                result=subprocess.run(invocation(phase,sha),cwd=HERE,stdout=stream,stderr=subprocess.STDOUT,timeout=remaining)
            if result.returncode==0:
                prior['completed'].append(phase); save(receipt,prior)
                print('PHASE '+phase+' done',flush=True)
                break
            ident=recover(HOME,log.read_text()[-16000:],prior['retries'])
            subprocess.run(invocation('resolve-reservation',sha,('--reservation-id',ident,'--reason','approved transient retry; full reservation spent','--count-as-spent')),
                           cwd=HERE,check=True,timeout=60)
            prior['retries']+=1;save(receipt,prior)
            print('RETRY '+phase+' reservation='+ident,flush=True)
        ledger=Ledger(HOME/'state/paid.jsonl',sha,plan['caps'])
        print('CASH cumulative='+str(plan['cash_caps'].get('prior_spent_usd',0)+sum(ledger.spent(s,'cash') for s in plan['caps'])),flush=True)
    prior.update(finished=time.time(),minutes=(time.time()-prior['started'])/60,status='completed')
    save(receipt,prior)
    subprocess.run([sys.executable,'-B',str(HERE/'summarize_place_write.py')],cwd=HOME/'state',check=True,timeout=60)
    print('PILOT COMPLETE minutes='+str(round(prior['minutes'],2)),flush=True)


if __name__=='__main__': main()
