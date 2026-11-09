# 0001 지식 스키마 — 로컬 검증 초안

SQL은 클라우드 배포 미승인이다. 로컬 일회용 컨테이너 적용은 별도 승인되었다. Supabase의 `extensions` 스키마와 PostgreSQL 15+ (`NULLS NOT DISTINCT`)가 선행 조건이다. `IF NOT EXISTS`는 스키마·확장에만 적용했다. 나머지 DDL과 시드는 중복 실행에 멱등하지 않다.

## 테이블별 목적
- `knowledge.host`: 프로젝트 식별과 교차 검색 허용 기본값.
- `knowledge.fragment`: host별 정본 조각 및 revision CAS의 대상 행.
- `knowledge.fragment_history`: 조각 변경 이력의 전체 행 JSON snapshot.
- `knowledge.relation`: 같은 host 조각 간 supersedes/conflicts-with 연결.
- `knowledge.work_unit`: 완료 신호와 partition별 작업 상태.
- `knowledge.work_unit_event`: 작업 상태 전이의 append-only 로그.
- `knowledge.ledger_entry`: judgement 원문과 fact 단위 검수의 상위 상태.
- `knowledge.ledger_entry_event`: 원장 상태 전이의 append-only 로그.
- `knowledge.check_receipt`: fact·stage·템플릿별 검수 패킷/응답·중복 키.
- `knowledge.absorption`: 정책/사람 처분과 정본 조각 연결.
- `knowledge.acceptance_policy`: host별/기본 정책의 순서·버전·조건·처분.
- `knowledge.confirmation_queue`: 사용자 확인 대기 사실과 답변.
- `knowledge.question_template`: 버전별 Jev 질문 wire map.

## 계약 대비 편차·경계
- 조각 초안의 `workspace` 테이블/`workspace_id` 및 그 활성 workspace 인덱스 없음: work_unit으로 통합하고 fragment는 정본만 둔다. 이 때문에 기존 16필드 중 workspace_id가 빠지고, 초안의 `fragment_embedding` 테이블도 빠진다(vector 확장만 준비). `confidence`의 정본 기본값은 `confirmed`; 코드의 `build_fragment`와 같고 초안의 candidate 기본값과 다르다.
- fragment.id는 UUID `gen_random_uuid()` 기본값이다. relation 양 끝, fragment_history.fragment_id, check_receipt.target_fragment_id, absorption.fragment_ref도 UUID FK이다. 파일 러너가 생성하는 UUID 문자열은 DB UUID로 전달한다.
- fragment_history의 `op`은 create/update/deprecate. INSERT AFTER 트리거는 생성된 행 전체, UPDATE BEFORE 트리거는 변경 전 행 전체를 JSON snapshot으로 남긴다. history.revision은 이벤트가 도달한 새 revision (create=1, update=NEW.revision), snapshot.revision은 변경 전 값이다. DELETE는 금지하고 active=false UPDATE는 deprecate로 분류한다. UPDATE는 OLD.revision+1만 허용한다. `set_config('knowledge.actor', '...', true)`와 `set_config('knowledge.absorption_id', '<uuid>', true)`를 같은 트랜잭션에서 설정하면 이력에 사용한다; 미설정/빈 값은 NULL, 비어 있지 않은 잘못된 UUID는 오류다. absorption FK는 deferred이므로 같은 트랜잭션에 absorption을 작성할 수 있다.
- absorption의 `rule_id`/`action`은 store.py와 일치한다. 러너와 다른 이름은 acceptance_policy의 저장 형태(`id/when/then` 대신 `rule_id/when_json/then_action`), fragment_history의 DB snapshot 이벤트와 파일 러너 이벤트(`fragment_ref/operation/fragment`), confirmation_queue의 식별자(`confirmation_id` 대 파일 `absorption_id`)이다. 이들은 각기 다른 저장 표현이며 DB column에 러너 객체를 직접 쓰는 writer는 별도 매핑이 필요하다.
- 스키마 델타 §3의 `packet` 익명화 설명은 §0의 익명화 폐지 결정으로 대체: anonymization_map 컬럼/참조 없음. `packet`의 현행 `evidence_quote`는 문자열이고 별도의 refs 배열은 선택적이다. `{type, locator, quote}` 검사(CHECK)는 `fragment.source.evidence_refs`, 원장 body 및 fact의 `evidence_refs`, 그리고 패킷 최상위 `evidence_refs`가 있을 때만 적용한다. type은 코드의 `evidence_source`(user_confirmed/user_tentative 포함)와 다른 참조 유형이다. 현재 러너의 일반 문자열 evidence_refs를 그대로 DB에 넣으면 CHECK를 통과하지 못할 수 있다. locator의 URL/경로 유형별 상세 패턴과 원문 진위는 검사하지 않는다.
- `acceptance_policy`의 tuple 조건은 JSON 배열로 표현했다. `when_json`은 코드의 `when`, `then_action`은 `then`의 저장 컬럼명이다. 첫 일치 우선/미일치 hold는 정책 평가기 책임이다. `UNIQUE NULLS NOT DISTINCT (host_id,version,ordinal)`로 기본 정책(host_id=NULL)에도 한 ordinal당 한 행만 허용한다. 정책 P01~P10은 `policy.py DEFAULT_POLICY`와 값/순서가 동일하다.
- intent v0.3.1은 update용 wire 질문만 시드했다. deprecate용 `invalidation_evidence`/`replacement_exists`는 주어진 경로에 원문 wire가 없어 미시드이며 해당 경로의 검수는 이 초안만으로 지원되지 않는다. 시드 출처 두 파일은 연구 규약상 active root 증거 파일이며 SQL에 절대경로 주석을 남겼다. record-need의 impact는 그 중 `jev-impact-stability-14-plan.json`에서, utterance_status는 `jev-utterance-status-15-plan.json`에서 복사했다.

## 제약·보안 이유
- relation은 `(host_id,src)`와 `(host_id,dst)` 복합 FK를 각각 fragment `(host_id,id)`에 연결한다. 경계 검사를 애플리케이션이나 변경 가능한 트리거 코드에 위임하지 않고 DB FK가 양 끝의 같은 host를 강제한다.
- 세 append-only 테이블은 하나의 BEFORE UPDATE/DELETE 거절 함수를 공유하는 세 트리거로 보호한다. fragment UPDATE의 revision 증가와 이전 snapshot 기록은 DB 트리거가 같은 문장 내에서 강제한다. 동시 writer의 기대 revision 검사는 여전히 `WHERE id=? AND revision=?` CAS가 필요하다. 이벤트 테이블 INSERT 자체의 완전성은 트리거만으로 보장되지 않는다.
- 모든 13개 테이블에 RLS를 켜고 정책은 만들지 않았다. `anon`·`authenticated`·`public`의 schema/table/sequence/function 권한을 제거한다. owner 또는 BYPASSRLS 서비스 역할만 접근하도록 하는 초안이며 실제 역할 부여/접속 설정은 포함하지 않는다. 이후 객체를 생성하면 별도 권한 설정이 필요하다.

## 알려진 미결정 및 적용 전 검증 제안
- 사용자/host 소유권·RLS 정책·인증, cross-partition 병합, proposal 상세 형식, 네트워크 단절 대기 버퍼, 시드하지 않은 deprecate 질문 및 저장 표현 매핑는 별도 확정이 필요하다. `digestion_receipt_id`가 실제 digestion stage이고 entry/fact가 일치하는지, fragment revision 및 snapshot은 트리거로 강제하지만 임의 이력 INSERT의 진위는 제한하지 않는다.
- 로컬 검증 명령/기대·실제 결과는 `test_0001_local.sh` 및 `test_0001_local.log`에 기록한다. 클라우드 DB는 대상에서 제외한다.
