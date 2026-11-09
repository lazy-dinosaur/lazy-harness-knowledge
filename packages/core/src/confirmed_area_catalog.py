"""Main-only writer for a user-confirmed ten-area list (this repository ships a neutral example list).
No model calls, draft adoption, database access, or inferred confirmation.
"""
import argparse
import sys
from safety import RUNNER, load, save, digest, Stop, exclusive
sys.path.insert(0, str(RUNNER))
from prepare import AREA_SCOPES, scope_home
from area_catalog import areas

# Neutral example catalog (public repository). A host supplies its own user-confirmed list; the lazy-harness host
# list and its confirmation stay in lazy-harness records.
CONFIRMED_AREAS = (
    ('예약', '예약 시간, 예약 변경, 반복 일정'),
    ('고객', '고객 정보, 담당자, 고객 응대 세션, 테넌트 경계'),
    ('채팅', '채팅방 공개 범위, 입력 모드, 반응, 알림, 메시지 표시'),
    ('업무', '할 일 분류, 회의 후속 조치, 대시보드 업무 현황'),
    ('문서기록', '문서 필수 항목, 옵션, 삭제 정책'),
    ('권한', '라이선스 등급 확인, 권한 부여 의미'),
    ('설정', '기본값, 자동완성 등 조직 운영 설정'),
    ('아키텍처', '아키텍처 용어, 정책 레지스트리, 런타임 전송, 개발 인스턴스 정책'),
    ('하네스', '문서 탐색, 옵션 게이트, 지침 승격, 기록 의사결정 절차'),
    ('인프라', 'CI 실행 환경, 테스트 전략, 브랜치, 배포, 데이터 롤아웃·복구'),
)


def write_confirmed(plan, home, approval):
    if plan.get('scope') not in AREA_SCOPES or not plan.get('area_catalog'):
        raise Stop('confirmed catalog only supports area pilots')
    if approval != digest(plan): raise Stop('exact plan SHA required')
    if plan['scope'] == 'pilot-area-10-isolated' and home.resolve() != scope_home(plan['scope']).resolve():
        raise Stop('confirmed catalog requires exact isolated home')
    if load(home/'state/plan.json') != plan: raise Stop('import-state must precede catalog')
    value = {'areas':[{'name':n,'description':d} for n,d in CONFIRMED_AREAS],
             'confirmed_by_user':True, 'confirmed_on':'2026-10-13', 'plan_sha':approval}
    areas(value)
    path = home/'state/area-catalog.json'
    if path.exists() and load(path) != value: raise Stop('existing confirmed catalog differs; do not overwrite')
    save(path, value)
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', required=True, choices=AREA_SCOPES)
    parser.add_argument('--approve-plan-sha', required=True)
    args = parser.parse_args()
    home = scope_home(args.scope)
    with exclusive(home):
        result = write_confirmed(load(home/'plan.json'), home, args.approve_plan_sha)
    print('confirmed catalog: '+str(len(result['areas']))+' areas; plan '+result['plan_sha'])
