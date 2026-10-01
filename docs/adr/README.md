# ADR 목록

결정 하나당 파일 하나를 쓰고, 번호는 순서대로 붙인다. 결정이 바뀌면 기존 ADR을 고치지 않고 새 ADR을 쓴 뒤, 기존 ADR의 상태를 "ADR-XXXX로 대체"로 바꾼다. 새 ADR은 `0000-template.md`를 복사해 시작한다.

| 번호 | 제목 | 상태 |
| --- | --- | --- |
| [0001](0001-scope-schema-design-only.md) | nlxpg 범위를 스키마 설계로 한정하고, 레코드 추출·적재는 별도 프로젝트로 분리 | 채택 |
| [0002](0002-onprem-qwen3-vllm-guided-decoding.md) | 온프레미스 Qwen3와 vLLM guided decoding으로 구조화 추출 | 제안 |
| [0003](0003-evaluation-semantic-schema-matching.md) | 의미 기반 매칭으로 스키마 복원도를 평가하고, 세 가지 데이터셋을 조합 | 제안 |
| [0004](0004-target-postgresql-shared-infra.md) | 대상 DB를 PostgreSQL로 한정하고 pgxnl 인프라를 공유 | 채택 |
| [0005](0005-naming-common-standard-postgresql-adaptation.md) | 명명·타입은 공공데이터 공통표준을 따르고, 날짜 타입과 물리명 대소문자만 PostgreSQL에 맞춘다 | 채택 |
| [0006](0006-web-ui-accounts-and-access.md) | 웹 UI 계정은 nlxpg 전용, 관리자·사용자 2역할, 실행 이력은 본인 것만 | 채택 |
| [0007](0007-llm-settings-in-db.md) | LLM 설정은 웹 UI에서 프로필로 등록해 시스템 DB에만 저장 | 채택 |
| [0008](0008-external-llm-for-non-internal-documents.md) | 사내 문서가 아닌 실행에는 외부 LLM API(Claude)를 골라 쓸 수 있다 | 채택 |
| [0009](0009-local-standard-additions.md) | 기관 추가 표준은 공통표준 원본과 분리해 시스템 DB에 두고, 원본 위에 덧붙인다 | 채택 |

## 결정 대기 중

아래 사항은 결정되면 ADR로 남긴다.

- 1차 대상 문서 유형과 도메인
- HWP/HWPX 파싱 방식
- 스키마 중간 표현(JSON) 형식
