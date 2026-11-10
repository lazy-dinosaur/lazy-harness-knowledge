"""Single authority for the user-calibrated DDD2 prompt (comparison 2)."""
import hashlib

DDD2_HEAD = (
 "용어: 도메인 = 업무 영역. 주어 = 그 도메인 안에서 사실들이 모이는 대상 이름. candidates[{i}] 의 name 을 "
 "area 도메인의 주어로 쓰는 것이 적합한가? example 문장은 이 이름이 쓰인 예시다. "
)
DDD_BODY = (
 "기준(DDD): 도메인은 경계 컨텍스트다. 주어는 그 안의 엔티티, 즉 상태가 바뀌어도 같은 대상으로 계속 추적되는 대상(또는 여러 대상을 묶는 대표)이다. "
 "주어 맞음: 화면·모달·컴포넌트, API, 데이터/테이블, 이름 붙은 서비스·도구·환경, 상태를 가진 업무 대상. "
 "문서들이 설명하는 서비스·앱·제품·플랫폼 자체의 이름(한글·영문 표기 모두)은 주어가 아니다. 서비스는 모든 도메인과 주어를 담는 그릇이고, 그 안의 화면·기능·API·데이터·업무 대상이 주어다. \"<서비스명>의 <대상>\"처럼 서비스가 다른 대상을 거느리면 주어는 <대상>이다. 우리 서비스가 가져다 쓰는 다른 회사의 외부 서비스·도구(오류 수집·결제·모델 API 제공자 등)는 주어가 될 수 있다. 고정 예시: \"ShopNow의 배송 조회 화면\"→배송 조회 화면(ShopNow는 서비스 자체라 주어 아님), \"ShopNow가 Stripe로 결제한다\"→Stripe는 외부 서비스라 주어 맞음. "
 "주어 아님(그것을 가진 대표가 주어다): 정책·규칙·기준·규율, 동작·처리·조정·생성·수정·배포(rollout)·흐름 같은 과정, "
 "역할·상태·값·권한 속성, 테스트·회귀 보호·감사·기록·전략 문서, 대상의 한 기능·설정, 화면의 부분·레이아웃·스크롤·색상. "
 "예시: '결제 확인 모달' 맞음, 'order.create API' 맞음, '주문' 맞음, '반품 신청' 맞음, 'Sentry' 맞음 / "
 "'주문 취소 정책'→주문, '배송 출고 처리'→배송, 'Sentry 알림 조정'→Sentry, '담당 매니저'→주문의 역할, "
 "'결제 회귀 테스트'→결제, '장바구니 쿠폰 적용'→장바구니, '확인 버튼'→모달 은 모두 주어 아님.")
DDD2 = DDD2_HEAD + DDD_BODY  # Jev question; bytes identical to calibrated text
DDD_TERMS = "용어: 도메인 = 업무 영역(경계 컨텍스트). 주어 = 그 도메인 안에서 사실들이 모이는 대상 이름. "
DDD_FOR_AUTHOR = DDD_TERMS + DDD_BODY  # Luna tasks: criterion only, no Jev question or {i} placeholder
# Task381 (user "진행해봐"): host/service-container clause, re-measured 53/56 & 52/56 + host cases (.local/stagewise/ddd2-probe3-result.json, copied to ddd-calibration-task381.json)
CALIBRATED_SHA256 = 'b2f3187aa770e2a7b07fdc3d26fde6f00528a109523e328451dd878fa50052fa'

DOMAIN_CRITERION = (
    # Task377 (user-confirmed 2026-11-07 "좋아 그렇게 가도록 하자"): a domain is a broad context with its own language and
    # responsibility, business OR technical/operational; components/screens/behaviors/document titles never are.
    '정의: 도메인은 여러 주어를 담는 큰 맥락(DDD 경계 컨텍스트)이다. 같은 용어와 책임의 경계를 기준으로 한다. '
    '업무 맥락뿐 아니라 자기 용어와 책임을 가진 넓은 기술·운영 맥락도 도메인이다. '
    '문서들이 설명하는 서비스·앱·제품·플랫폼 자체(그 이름)는 모든 도메인을 담는 그릇이지 도메인이 아니다. '
    '화면·모달·컴포넌트·API·도구·파일은 주어이며 절대로 도메인이 아니다. 정책/동작/부분의 이름과 문서 제목도 도메인이 아니다. '
    '기능이나 화면이 달라도 같은 맥락이면 같은 도메인이다. 관련 있다는 이유만으로 다른 맥락을 같다고 하지 않는다. '
    # Task380 (user "응 추천대로 가줘"): decide by the owner entity's business context, never by the described action.
    '도메인은 지식의 소유 엔티티(화면·모달·API·데이터·업무 대상)가 속한 맥락으로 정한다. 지식이 설명하는 동작·기능(조회·정렬·입력·알림·표시 등)으로 정하지 않는다. '
    '동작·기능 명사는 여러 화면에서 쓰여도 도메인 이름이 아니다. 고정 예시: "배송 조회 화면의 정렬 규칙"은 물류 도메인이고 조회·정렬은 도메인이 아니다. '
    '고정 예시(시험 문서와 무관): 물류=도메인, 재고=도메인, 인사=도메인, 모니터링=도메인(기술·운영 맥락); '
    '배송 확인 모달·StockGrid 컴포넌트·parcel.create API·ParcelProbe 도구·stock.py 파일=주어, 도메인 아님. '
    '배송 확인 모달과 출고 전표는 물류 안의 서로 다른 주어지만 같은 도메인이다. '
    # Task384 (user "진행해줘", SDD v2-knowledge-ddd-structure): shared kernel / generic subdomain; uses != covers.
    '여러 도메인이 함께 가져다 쓰는 공용 기능·컴포넌트들의 묶음은 자기 용어와 책임이 있으면 별도 도메인(공유 커널·일반 하위 도메인)이 될 수 있다. '
    '그 이름은 묶음의 맥락 명사이고, 그 안의 개별 컴포넌트는 주어다. 여러 도메인이 가져다 쓰는 것(uses)은 그 도메인들을 덮는 것(covers)이 아니다. '
    '고정 예시: 여러 업무 화면이 함께 쓰는 공통 UI 컴포넌트 묶음=디자인시스템 도메인, 그 안의 Button·Modal=주어. ')
DOMAIN_QUESTION = DOMAIN_CRITERION + 'candidate의 name/description은 도메인(업무 또는 기술·운영 맥락) 이름으로 적합한가? 예/아니오 확률로 답한다.'
DOMAIN_SAME_QUESTION = DOMAIN_CRITERION + ('candidate와 proposed가 같은 맥락(도메인)을 가리키는가? '
    '이름·설명·unit 전체를 비교한다. 같으면 기존 영역을 재사용한다. 예/아니오 확률로 답한다.')

def criterion_sha():
    return hashlib.sha256(DDD2.encode()).hexdigest()

def assert_calibrated():
    if criterion_sha() != CALIBRATED_SHA256:
        raise ValueError('DDD2 criterion drift: recalibrate and obtain approval before updating the frozen hash')
