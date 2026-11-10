"""Area catalog: one draft call, explicit Main confirmation, then domain seeds."""
import re
from safety import load, save, digest, Stop
from prepare import source_home

PILOT_TEN = ('R11','R21','R08','R12','R20','R03','R13','R22','R36','R05')

def areas(value):
    rows=value.get('areas')
    if not isinstance(rows,list) or not 1 <= len(rows) <= 30: raise Stop('invalid area catalog')
    seen=set()
    for row in rows:
        if not isinstance(row,dict): raise Stop('invalid area')
        name=row.get('name'); description=row.get('description')
        from subject_contract import short_name_errors
        errors=short_name_errors(name,area=True)
        if errors: raise Stop(str(errors))
        if len(name)>60: raise Stop('invalid area name')
        key=re.sub(r'[\W_]+','',name).casefold()
        if not key or key in seen: raise Stop('duplicate area')
        seen.add(key)
        if not isinstance(description,str) or not 1<=len(description.strip())<=300 or '\n' in description: raise Stop('invalid area description')
    return rows

def draft(plan,home,paid):
    import pipeline as p
    path=home/'state/area-catalog-draft.json'
    if path.exists(): return load(path)
    # Use all 55 pilot documents even for the ten-document screen.
    from prepare import inputs
    records,_=inputs('pilot-area')
    docs=[]
    for r in records:
        text=(source_home(home)/'v1'/r['path']).read_text()
        docs.append({'path':r['path'],'title':r['title'],
                     'headings':[line[:240] for line in text.splitlines() if line.startswith('#')][:8]})
    paid.scope='area-catalog'
    result,_=p.packet_model(paid,'area-catalog',{'documents':docs},
        '문서 전체를 업무 영역 약 5~15개로 묶는 초안. 기능이 달라도 같은 업무 영역이면 하나. '
        '영역 이름은 한 단어·개념 하나, 공백/및/·/슬래시/쉼표 금지. 문서 제목을 영역 이름으로 복사하지 않는다. JSON만 {"areas":[{"name":"캘린더","description":"한 줄 업무 영역 설명"}]}.',charge_step='maintenance')
    result={'areas':areas(result),'documents_sha':digest(docs),'plan_sha':digest(plan)}
    save(path,result);return result

def confirmed(plan,home):
    path=home/'state/area-catalog.json'
    if not path.exists(): raise Stop('Main must obtain user confirmation and write state/area-catalog.json')
    value=load(path)
    if value.get('confirmed_by_user') is not True or value.get('plan_sha')!=digest(plan):
        raise Stop('catalog requires confirmed_by_user=true and exact plan_sha')
    return areas(value)

def install(plan,home,dsn,host,encode):
    import store_pg as pg, domain_router as dr, subject_dict as sd, embed
    rows=confirmed(plan,home)
    vectors=encode([sd.identity_text(r['name'],r['description']) for r in rows])
    with pg.connect(dsn) as conn,conn.cursor() as cur:
        for row,vector in zip(rows,vectors):
            dr.ensure(cur,host,row['name'],row['description'])
            cur.execute('''insert into knowledge.domain_embedding(host_id,domain,model_id,embedding)
                values(%s,%s,%s,%s::extensions.vector) on conflict(host_id,domain,model_id)
                do update set embedding=excluded.embedding''',(host,row['name'],embed.MODEL_ID,sd._vec(vector)))

def name_area(paid,text,hint):
    import pipeline as p
    result,_=p.packet_model(paid,'new-area-'+digest(text)[:20],{'document':text,'hint_only':hint},
        '기존 어느 영역에도 속하지 않는 문서다. 업무 영역 이름은 한 단어·개념 하나, 공백/및/·/슬래시/쉼표 금지. 세부는 한 줄 설명에 둔다. '
        '문서 제목/hint를 그대로 복사하지 않는다. JSON {"areas":[{"name":"영역","description":"설명"}]}',charge_step='maintenance')
    return areas(result)[0]

def relabel(record,generated,root):
    """Only metadata changes; preserve paid claims, subjects, fixes, and all receipts."""
    import copy
    from migration_evidence import classify
    out=copy.deepcopy(generated)
    text=(root/record['path']).read_text(); lines=text.splitlines()
    source=classify(record['path'],text)['evidence_source']
    for fact in out.get('facts',[]):
        refs=fact.get('evidence_refs',[])
        if not refs: raise Stop('migration fact has no source citations')
        for ref in refs:
            locator=ref.get('locator','')
            match=re.search(r'#L(\d+)-L(\d+)@sha256:([a-f0-9]+)$',locator)
            if not match or not locator.split('#')[0].endswith('/v1/'+record['path']) or match[3]!=record['sha256']:
                raise Stop('migration citation binding mismatch')
            a,b=map(int,match.group(1,2))
            if not 1<=a<=b<=len(lines) or ref.get('quote')!='\n'.join(lines[a-1:b]): raise Stop('migration quote mismatch')
            ref['type']=source
        fact['evidence_source']=source
    out['migration_evidence']=classify(record['path'],text)
    return out
