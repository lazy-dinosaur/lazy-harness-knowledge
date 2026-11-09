"""Feature-level subject metadata and mechanical code-evidence candidates.
Candidates are navigation cues, not semantic assignments or newly registered objects.
"""
import re
from contextlib import contextmanager
from contextvars import ContextVar

# Caller-owned experiment scope; never enabled by worker fact fields or environment.
_UNIT_FIRST = ContextVar('unit_first_metadata', default=False)

@contextmanager
def unit_first_metadata():
    token = _UNIT_FIRST.set(True)
    try:
        yield
    finally:
        _UNIT_FIRST.reset(token)

from pathlib import PurePosixPath

SHORT_NAME_GUIDANCE = ('영역 이름은 한 단어·개념 하나. 주어 이름은 1~3단어·개념 하나, 영역 이름과 달라야 한다. '
    '및/·/슬래시/쉼표로 개념을 나열하지 않는다. 세부 내용은 한 줄 설명에 둔다. '
    '기존 긴 이름은 aliases로 보존하고 사실 문장은 바꾸지 않는다. 사실에 짧은 이름 또는 별칭이 verbatim으로 있어야 한다. ')

def short_name_errors(name, *, area=False):
    prefix = 'E_AREA' if area else 'E_SUBJECT'
    errors = []
    if not isinstance(name, str) or not name.strip() or not 1 <= len(name.split()) <= (1 if area else 3) or (area and re.search(r'\s', name)):
        errors.append({'code':prefix+'_NAME_LENGTH','where':'domain' if area else 'subject',
                       'detail':'area: one word; subject: 1..3 words'})
    if isinstance(name, str) and (re.search(r'및|[·/,\r\n]|(?:^|\s)(?:and|or|와|과)(?:\s|$)', name, re.I) or re.search(r'\S[와과]\s+\S', name)):
        errors.append({'code':prefix+'_NAME_JOIN','where':'domain' if area else 'subject',
                       'detail':'one concept; no conjunction/list separators'})
    return errors

def subject_in_fact(fact, aliases=()):
    names = [fact.get('subject')]
    declared = fact.get('aliases', [])
    if isinstance(declared, list): names.extend(declared)
    names.extend(aliases)
    return any(isinstance(n, str) and n.strip() and n in str(fact.get('fact', '')) for n in names)

SUBJECT_TYPES = ('기능', '화면', 'API', '데이터', '규칙/정책', '작업 흐름', '외부 연동')


def metadata_errors(fact):
    errors=[]
    kind=fact.get('subject_type')
    if not _UNIT_FIRST.get() and (not isinstance(kind,str) or kind not in SUBJECT_TYPES):
        errors.append({'code':'E_SUBJECT_TYPE','where':'subject_type',
                       'detail':'exactly one subject type required: ' + ' | '.join(SUBJECT_TYPES)})
    description=fact.get('subject_description')
    if not isinstance(description,str) or not description.strip() or len(description)>300 or '\r' in description or (not _UNIT_FIRST.get() and '\n' in description) or (_UNIT_FIRST.get() and not 1<=len(description.splitlines())<=2):
        errors.append({'code':'E_SUBJECT_DESCRIPTION','where':'subject_description',
                       'detail':'one-line feature description required (1..300 chars); <domain>의 <subject> names one place to change'})
    if 'subject' in fact: errors.extend(short_name_errors(fact['subject']))
    if _UNIT_FIRST.get() and (fact.get('parent_subject') or fact.get('parent_subject_id')):
        errors.append({'code':'E_SUBJECT_PARENT','where':'parent_subject','detail':'unit-first has only domain > subject; no parents'})
    errors.extend(hierarchy_errors(fact))
    return errors


def grounding_errors(fact, source_text='', existing_subjects=()):
    """Lexical-only compatibility API. Existing names/aliases are never proof."""
    from source_grounding import check_names
    return check_names(fact, str(source_text))['errors']


PARENT_TYPES = ('기능', '작업 흐름', '외부 연동', '화면')
HIERARCHY_GUIDANCE = (SHORT_NAME_GUIDANCE + '한 사실은 가장 구체적인 subject 하나. 같은 도메인 상위 기능은 parent_subject '
    '{name, subject_type, description} 또는 null. 상위 종류는 기능/작업 흐름/외부 연동/화면, 상위의 상위 금지. '
    '이름에 계층을 넣지 말고 선행 <domain>의 를 제거한다. 가게의 주문 접수 기능은 '
    'parent_subject.name=주문, subject=접수. existing_objects의 parent›subject 이름/종류/설명을 재사용. '
    '다른 대상 관계는 leaf link로 유지한다. 예시는 가상이며 복사 금지. '
    '정본 문장에는 주어만 그대로 쓰고 영역은 별도 칸이다. subject/parent는 원문 토큰 또는 원문 전용 Jev 검사로 검증한다(E_SUBJECT_UNGROUNDED). ')


def packed_name(name, domain=None, parent=None):
    if not isinstance(name, str): return False
    if any(mark in name for mark in (' › ', ' > ', ' → ')): return True
    if domain and name.startswith(domain + '의 '): return True
    # Explicit possessive prefix is packed even when an old generator copied a
    # different domain label from its example. Do not infer a parent from it.
    if re.match(r'^\S{2,60}의\s+\S', name): return True
    if isinstance(parent, dict) and parent.get('name'):
        return name.startswith(parent['name'] + ' ') or name.startswith(parent['name'] + '의 ')
    return False


def hierarchy_errors(fact):
    parent = fact.get('parent_subject')
    errors = []
    domain = fact.get('assigned_area') or (None if fact.get('domain_is_hint') else fact.get('domain'))
    key = lambda value: re.sub(r'[\W_]+', '', str(value or '')).casefold()
    from area_name_policy import allowed_fact
    if domain and key(fact.get('subject')) == key(domain) and not allowed_fact(fact):
        errors.append({'code':'E_SUBJECT_AREA','where':'subject','detail':'subject must be a feature inside the area, not the area itself'})
    if isinstance(parent, dict) and domain and key(parent.get('name')) == key(domain):
        errors.append({'code':'E_SUBJECT_PARENT','where':'parent_subject','detail':'parent feature cannot equal its area'})
    if packed_name(fact.get('subject'), domain, parent):
        errors.append({'code':'E_SUBJECT_HIERARCHY','where':'subject','detail':'store parent separately; strip leading <domain>의 '})
    if parent is not None:
        valid = (isinstance(parent, dict) and isinstance(parent.get('name'), str)
                 and bool(parent['name'].strip()) and len(parent['name']) <= 60
                 and parent.get('subject_type') in PARENT_TYPES
                 and isinstance(parent.get('description'), str) and 0 < len(parent['description'].strip()) <= 300
                 and not any(c in parent['description'] for c in '\r\n')
                 and not parent.get('parent_subject') and not parent.get('parent_subject_id')
                 and not any(c in parent['name'] for c in '\r\n')
                 and parent['name'] != fact.get('subject')
                 and (not parent.get('domain') or parent['domain'] == domain)
                 and not packed_name(parent['name'], domain))
        if valid:
            import runner
            valid = runner.subject_error(parent['name']) is None and not short_name_errors(parent['name'])
        if not valid:
            errors.append({'code':'E_SUBJECT_PARENT','where':'parent_subject','detail':'one root parent in same domain; permitted type, name and description required'})
    return errors


# Root-relative paths explicitly cited in text/locators only. No filesystem crawling or semantic search.
PATH = re.compile(r'(?<![\w/])(?:[\w@.-]+/)+[\w@.\[\]-]+(?:\.[A-Za-z0-9]+)?')


def code_candidates(evidence, limit=24):
    rows=[];seen=set()
    text=re.sub(r'https?://[^\s"<>]+','',evidence if isinstance(evidence,str) else '')
    for match in PATH.finditer(text):
        raw=match.group().rstrip('.')
        path=PurePosixPath(raw)
        if '..' in path.parts or raw.startswith(('http:','https:')): continue
        parts=path.parts
        code_suffix=path.suffix.lower() in {'.ts','.tsx','.js','.jsx','.vue','.svelte','.py','.go','.rs','.java','.kt','.sql','.cs','.rb','.php','.c','.cpp','.h'}
        feature_path=any(part in parts for part in ('features','feature','modules','domains','components','routers','routes'))
        if not code_suffix and (path.suffix or not feature_path):continue
        offers=[]
        for marker in ('features','feature','modules','domains'):
            if marker in parts and parts.index(marker)+1 < len(parts):
                stop=parts.index(marker)+2
                folder='/'.join(parts[:stop]);offers.append((folder,'feature_folder',parts[stop-1]))
        stem=path.stem
        is_test=any(x in parts for x in ('tests','__tests__')) or bool(re.search(r'(?:^test_|\.(?:test|spec)$)',stem))
        if not is_test:
            if 'router' in stem.lower() or any(x in parts for x in ('routes','routers')):
                offers.append((raw,'router',stem))
            elif path.suffix in ('.tsx','.jsx','.vue','.svelte') or 'components' in parts:
                offers.append((raw,'component',stem))
        if not offers and len(parts)>1:
            folder=str(path.parent)
            if path.parent.name not in ('tests','__tests__','src','lib'):
                offers.append((folder,'folder',path.parent.name))
        for target,role,name in offers:
            key=(target,role)
            if key in seen:continue
            seen.add(key);rows.append({'path':target,'name':name,'role':role,'evidence_path':raw,
                                      'status':'candidate_only; choose owning feature, never the test/action/value'})
            if len(rows)>=limit:return rows
    return rows
