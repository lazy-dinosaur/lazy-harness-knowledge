"""Opt-in HOST-owned future review authority. Trusts same-UID localhost, not malware.

Private SQLite journal serializes review reservation/consumption, NOT business effects.
Business writes retain their own transaction/CAS/idempotency. No cross-store atomicity.
No legacy receipt import, pickle, query/source flags, key regeneration or import-time IO.
"""
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import time
from writer_worker import digest

FORMAT='host-durable-role-review/1'
ROLES={'source-owner-hypothesis-review':'source-owner-hypothesis/v1',
       'domain-source-ambiguity-review':'sdk-domain-approval/v1',
       'normal-source-fidelity-review':'sdk-normal-admission/v1'}


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def safe_path(path,private=False):
    path=Path(path)
    if not path.is_absolute() or path!=Path(os.path.abspath(path)):
        raise ValueError('absolute canonical owned path required')
    for node in [*reversed(path.parents),path]:
        st=node.lstat()
        if stat.S_ISLNK(st.st_mode):raise ValueError('symlink forbidden')
        if node==path:
            if st.st_uid!=os.getuid() or st.st_nlink!=1 and not stat.S_ISDIR(st.st_mode):
                raise ValueError('store/key ownership or hardlink invalid')
            if private and stat.S_IMODE(st.st_mode)&0o077:raise ValueError('private permissions required')
        if stat.S_ISDIR(st.st_mode) and st.st_mode&0o022:
            # Sticky shared ancestors (/tmp) are acceptable only for isolated offline fixtures.
            if not st.st_mode&stat.S_ISVTX:raise ValueError('writable path ancestor forbidden')
    return path


def initialize(root,directory):
    """Explicit trusted HOST provisioning only. Existing incomplete stores fail closed."""
    root=safe_path(root)
    directory=Path(directory)
    if not directory.is_relative_to(root/'.local') or directory==root/'.local':
        raise ValueError('durable private store must be inside approved root/.local')
    if directory.exists() or directory.is_symlink():raise ValueError('store already exists; never regenerate missing key')
    safe_path(directory.parent)
    directory.mkdir(mode=0o700)
    safe_path(directory,True)
    fd=os.open(directory/'issuer.key',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:os.write(fd,secrets.token_bytes(32));os.fsync(fd)
    finally:os.close(fd)
    fd=os.open(directory/'reviews.sqlite',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    os.close(fd)
    with sqlite3.connect(directory/'reviews.sqlite') as db:
        db.execute('create table reviews (key text primary key, request text not null, state text not null, status text not null, envelope text, reason text, operation text)')
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


class DurableReceipt(dict):
    """New format, never a legacy Receipt with a resealed MAC."""
    def __init__(self,envelope,scope):
        super().__init__(copy.deepcopy(envelope['payload']))
        self._envelope=copy.deepcopy(envelope)
        self._scope=scope
        self._request=copy.deepcopy(scope.request);self._role=scope.role
    def __deepcopy__(self,memo):
        value=DurableReceipt(self._envelope,self._scope)
        value._request=copy.deepcopy(self._request);value._role=self._role
        value.clear();value.update(copy.deepcopy(dict(self),memo))
        return value
    def verified(self):
        try:
            if dict(self)!=self._envelope['payload']:return False
            self._scope.store.validate(self._envelope,self._request,self._role,consume=True)
            return dict(self)==self._envelope['payload']
        except (ValueError,OSError,sqlite3.Error,KeyError,TypeError):return False


class DurableReviewStore:
    """HOST startup capability; never constructed from source/query/model JSON."""
    def __init__(self,root,directory,host_id,issuer,current_state,operation_id,ttl_seconds=3600):
        self.root=safe_path(root)
        self.directory=safe_path(directory,True)
        if not self.directory.is_relative_to(self.root/'.local') or self.directory==self.root/'.local':
            raise ValueError('store outside approved root/.local')
        if not all(isinstance(v,str) and v for v in (host_id,issuer,operation_id)) or not callable(current_state):
            raise ValueError('explicit trusted host/issuer/current-state/operation required')
        if type(ttl_seconds) is not int or not 1<=ttl_seconds<=86400:raise ValueError('bounded expiry required')
        self.host_id,self.issuer,self.current_state,self.operation_id=host_id,issuer,current_state,operation_id
        self.ttl=ttl_seconds
        self._key=self._read_key()
        self._check_db()
    def _read_key(self):
        path=safe_path(self.directory/'issuer.key',True)
        if not stat.S_ISREG(path.stat().st_mode):raise ValueError('regular issuer key required')
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            if os.fstat(fd).st_ino!=path.stat().st_ino:raise ValueError('key path changed')
            key=os.read(fd,33)
        finally:os.close(fd)
        if len(key)!=32:raise ValueError('missing/invalid issuer key')
        return key
    def _check_db(self):
        safe_path(self.directory,True)
        path=safe_path(self.directory/'reviews.sqlite',True)
        if not stat.S_ISREG(path.stat().st_mode):raise ValueError('regular journal required')
        if not hmac.compare_digest(self._key,self._read_key()):raise ValueError('issuer key changed')
        return path
    from contextlib import contextmanager
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self._check_db(),timeout=10,isolation_level=None)
        try:
            db.execute('pragma synchronous=FULL')
            yield db
        except BaseException:
            db.rollback()
            raise
        finally:db.close()
    def scope(self,host_id):
        if host_id!=self.host_id:raise ValueError('durable host mismatch')
        return DurableReviewScope(self)
    def _mac(self,body):return hmac.new(self._key,canonical(body).encode(),hashlib.sha256).hexdigest()
    def validate(self,envelope,request,role,consume=False):
        if not isinstance(envelope,dict) or set(envelope)!={'body','mac','payload'}:
            raise ValueError('new durable envelope required; legacy JSON forbidden')
        body=envelope['body'];payload=envelope['payload']
        required={'format','root','host','issuer','role','provider','model','request_digest','payload_digest','response_digest','semantic_digest','state','issued','expires','key'}
        if not isinstance(body,dict) or set(body)!=required or not isinstance(envelope['mac'],str):raise ValueError('durable envelope schema')
        expected={'format':FORMAT,'root':str(self.root),'host':self.host_id,'issuer':self.issuer,'role':role,
                  'request_digest':digest(request),'payload_digest':digest(payload),'state':digest(self.current_state())}
        if role not in ROLES or payload.get('type')!=ROLES[role] or any(body.get(k)!=v for k,v in expected.items()):raise ValueError('current root/role/source/catalog/policy/candidate/state binding mismatch')
        p=payload.get('provenance',{})
        if body['provider']!=p.get('provider') or body['model']!=p.get('model') or p.get('role')!=role or p.get('input_binding')!=request['inputBinding']:
            raise ValueError('durable provider/role/request provenance mismatch')
        now=int(time.time())
        if (type(body['issued']) is not int or type(body['expires']) is not int or body['issued']>now or
            not body['issued']<body['expires'] or body['expires']<=now or body['expires']-body['issued']>self.ttl):raise ValueError('durable expired/future timestamp')
        if not hmac.compare_digest(self._mac(body),envelope['mac']):raise ValueError('durable authentication failed')
        with self.connect() as db:
            db.execute('begin immediate')
            row=db.execute('select status,envelope,operation from reviews where key=?',(body['key'],)).fetchone()
            if not row or row[0]!='issued' or row[1]!=canonical(envelope):raise ValueError('durable journal mismatch')
            if row[2] not in (None,self.operation_id):raise ValueError('other-operation replay forbidden')
            if consume and row[2] is None:db.execute('update reviews set operation=? where key=? and operation is null',(self.operation_id,body['key']))
            db.commit()
        return copy.deepcopy(payload)


class DurableReviewScope:
    def __init__(self,store):
        self.store=store;self.request=None;self.role=None;self._raw=None
    def record(self,response):self._raw=copy.deepcopy(response)
    def retain_hold(self,policy,req,semantic,reason):
        if not isinstance(reason,str) or not reason.strip():raise ValueError('negative reason required')
        key=digest([policy.host_id,policy.provider,policy.model,req['role'],semantic])
        with self.store.connect() as db:
            db.execute('begin immediate')
            prior=db.execute('select status from reviews where key=?',(key,)).fetchone()
            if prior and prior[0]=='issued':raise ValueError('cannot import/rewrite positive authority')
            db.execute('insert or ignore into reviews values(?,?,?,?,?,?,?)',(key,canonical(req),digest(self.store.current_state()),'hold',None,reason,None));db.commit()
    def review(self,policy,req,semantic,evaluate):
        import source_role_contract as c
        if req['role'] not in ROLES or policy.host_id!=self.store.host_id:raise ValueError('unsupported durable authority')
        key=digest([policy.host_id,policy.provider,policy.model,req['role'],semantic])
        self.request=copy.deepcopy(req);self.role=req['role']
        state=digest(self.store.current_state())
        with self.store.connect() as db:
            db.execute('begin immediate')
            row=db.execute('select request,state,status,envelope from reviews where key=?',(key,)).fetchone()
            if row:
                db.commit()
                if row[0]!=canonical(req) or row[1]!=state:raise ValueError('prior semantic review retained across protocol/state change; no shopping')
                if row[2]!='issued':raise ValueError('persistent prior negative/incomplete review HOLD; no retry')
                envelope=c.strict_json(row[3]);self.store.validate(envelope,req,req['role'])
                return DurableReceipt(envelope,self)
            db.execute('insert into reviews values(?,?,?,?,?,?,?)',(key,canonical(req),state,'reserved',None,'attempt reserved before provider',None));db.commit()
        try:
            self._raw=None
            receipt=evaluate()
            # Only THIS completed current typed validation can issue; no public JSON importer.
            if not c.verified(receipt) or type(receipt) is not c.Receipt or self._raw is None or receipt.get('request')!=req:
                raise ValueError('genuine current typed validated role result required')
            payload={k:copy.deepcopy(v) for k,v in receipt.items() if k!='_seal'}
            positive=(payload.get('accepted') is True if req['role']=='normal-source-fidelity-review' else
                      payload['review'].get('verdict')=='corrected' if req['role']=='source-owner-hypothesis-review' else payload['review'].get('choice') in ('new','reuse-existing'))
            if not positive:
                # Preserve ordinary role semantics for THIS validated negative result.
                # It is never durable positive authority and cannot pass consume/require/apply.
                with self.store.connect() as db:
                    db.execute("update reviews set status='hold',reason=? where key=? and status='reserved'",('validated current negative role result',key))
                return receipt
            now=int(time.time())
            body={'format':FORMAT,'root':str(self.store.root),'host':self.store.host_id,'issuer':self.store.issuer,'role':req['role'],
                  'provider':policy.provider,'model':policy.model,'request_digest':digest(req),'payload_digest':digest(payload),
                  'response_digest':digest(self._raw),'semantic_digest':digest(semantic),'state':state,'issued':now,'expires':now+self.store.ttl,'key':key}
            envelope={'body':body,'mac':self.store._mac(body),'payload':payload}
            with self.store.connect() as db:
                db.execute('begin immediate')
                if digest(self.store.current_state())!=state:raise ValueError('current state changed during review')
                changed=db.execute("update reviews set status='issued',envelope=?,reason=null where key=? and status='reserved'",(canonical(envelope),key)).rowcount
                if changed!=1:raise ValueError('concurrent review reservation lost')
                db.commit()
            return DurableReceipt(envelope,self)
        except BaseException as exc:
            with self.store.connect() as db:
                db.execute("update reviews set status='hold',reason=? where key=? and status='reserved'",(type(exc).__name__+': '+str(exc),key))
            raise
