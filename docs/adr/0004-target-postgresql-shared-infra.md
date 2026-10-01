# ADR-0004: 대상 DB를 PostgreSQL로 한정하고 pgxnl 인프라를 공유

- 상태: 채택
- 날짜: 2026-09-30
- 결정자: Youngki Ku

## 상황

생성할 DDL은 특정 DBMS의 문법과 타입 체계를 따라야 한다. 여러 DBMS를 동시에 지원하면 타입 매핑과 검증 로직이 DBMS 수만큼 늘어난다.

pgxnl은 이미 PostgreSQL 기반으로 구축되어 있다. BIRD DB를 PostgreSQL로 이관했고, PostgreSQL 전용 ANTLR4 SQL 문법을 갖추고 있다. 프로젝트 이름 nlxpg(NL → PG)도 이 방향을 전제로 한다.

## 결정

nlxpg의 출력은 PostgreSQL DDL로 한정한다. 평가용 DB, 샌드박스 실행 환경, SQL 문법 검증은 pgxnl과 공유한다.

## 검토한 대안

| 대안 | 장점 | 단점 | 채택 여부 |
| --- | --- | --- | --- |
| DBMS 중립 중간 표현 + 여러 DBMS용 DDL 생성 | 범용성 | 검증·타입 매핑 비용 증가, 초기 속도 저하 | 미채택 (추후 확장 가능) |
| PostgreSQL 전용 | pgxnl 인프라 재사용, 검증 단순화 | 다른 DBMS 적용 시 변환 필요 | **채택** |

## 결과

- 샌드박스 PostgreSQL에서 DDL을 실제로 실행해 검증할 수 있다.
- pgxnl의 ANTLR4 문법을 린트와 구문 검증에 재활용할 수 있다.
- 내부적으로는 DBMS 중립적인 스키마 중간 표현(JSON)을 두고 DDL은 마지막에 생성하도록 설계해, 향후 다른 DBMS로 확장할 여지를 남긴다.
