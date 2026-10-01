# nlxpg

업무 문서에서 PostgreSQL 스키마 초안(테이블 정의서, ERD, DDL)을 만드는 도구입니다. 명명·타입에는 행정안전부 공공데이터 공통표준을 적용하고, DDL은 샌드박스에서 실행해 검증합니다. pgxnl(Text-to-SQL)의 역방향(NL → PG)입니다.

- 버전: **0.5.0** — 시험판. 정답 스키마 대비 정량 평가 전입니다 ([변경 이력](CHANGELOG.md))
- 처음이라면 [소개 문서](docs/overview.md)부터 읽고, 사용법은 [사용자 매뉴얼](docs/user-guide.md)을 봅니다

## 기능

| 기능 | 내용 |
| --- | --- |
| 설계 | 문서 여러 개 → 섹션별 LLM 추출(JSON Schema 강제) → 문서 간 정합 → PK·FK·N:M·타입 → 공통표준 명명 → DDL·ERD·정의서 |
| 근거 | 모든 테이블·컬럼에 원문 위치를 붙이고, 화면에서 원문으로 바로 이동 |
| 검증 | 샌드박스 DDL 실행(항상 롤백), 린트, 정규형(1NF·2NF·3NF, 관계 누락, 파생 속성) |
| 표준 검증 | 기존 DDL·운영 DB·실행 결과의 공통표준 준수율과 매핑정보 CSV (영문 이름만 있어도 추정) |
| 기관 표준 | 공통표준 원본은 그대로 두고 기관 이음동의어·단어·용어를 추가해 함께 적용 ([ADR-0009](docs/adr/0009-local-standard-additions.md)) |
| 사람 검토 | 6개 항목 점수와 의견 기록 |
| 웹 UI | 로그인, 관리자·사용자 역할, 실행 이력, 모델 설정, 디버그 모드(LLM 호출 기록) |
| LLM | 온프렘 vLLM(OpenAI 호환), watsonx. 사내 문서가 아닌 실행에 한해 Claude ([ADR-0008](docs/adr/0008-external-llm-for-non-internal-documents.md)) |

## 설치

Ubuntu 서버 기준이고, 파이썬 3.12 이상과 PostgreSQL 12 이상이 필요합니다. 자세한 내용은 [docs/install.md](docs/install.md)에 있습니다.

```bash
git clone https://github.com/zoline/nlxpg.git && cd nlxpg
deploy/install.sh --pg-local --service      # 가상환경, 시스템 DB 생성, 스키마, 첫 관리자, systemd
```

설치한 뒤에는 다음 순서로 시작합니다.

1. `http://<서버>:8200`에 관리자로 로그인합니다. 초기 비밀번호는 설치 출력에 나옵니다.
2. **관리 → 모델 설정**에서 LLM 프로필을 등록합니다.
3. **새 설계**에서 문서를 올립니다. 처음이라면 [사용자 매뉴얼 2장](docs/user-guide.md#2-따라-하기-한빛항공사-예제)의 예제를 따라 합니다.

LLM 설정은 시스템 DB에만 저장합니다([ADR-0007](docs/adr/0007-llm-settings-in-db.md)). `.env`에는 시스템 DB 접속(`NLXPG_SYSTEM_PG_DSN`)과 암호화 키(`NLXPG_SECRET_KEY`)만 있으면 됩니다. 나머지 항목은 [.env.example](.env.example)에 있습니다.

| 선택 설치 | 명령 |
| --- | --- |
| PDF·DOCX 파싱 (Docling) | `.venv/bin/pip install -e '.[parsing]'` |
| Claude (외부 API) | `deploy/install.sh --with-claude` |

## CLI

```bash
nlxpg check                                  # LLM·시스템 DB 접속 확인
nlxpg design 문서.md -o out/x [--debug]      # 설계 (화면 없이). DB 없이: --no-store --no-sandbox
nlxpg standards lookup 거래처명 주문일자      # 이름 → 표준 물리명·도메인
nlxpg standards check --ddl schema.sql       # 표준 검증 (--dsn, --run도 가능)
nlxpg normalize --run 2                      # 정규형 검사
nlxpg runs list | delete N                   # 실행 이력
nlxpg users list | add | passwd | set        # 웹 UI 사용자
nlxpg setup llm                              # 화면 없이 LLM 프로필 등록
nlxpg db init                                # 시스템 DB 스키마 적용 (재실행 안전)
nlxpg serve                                  # 웹 UI (기본 포트 8200)
```

`design`의 출력은 `schema.json`(중간 표현), `schema.sql`, `erd.mmd`, `schema.md`, `validation.json`입니다. `--debug`를 붙이면 `llm_calls.json`도 함께 씁니다.

## 개발

```bash
uv venv --python 3.12 .venv
uv pip install -p .venv -e '.[dev]'
.venv/bin/ruff check nlxpg tests && .venv/bin/pytest -q
```

- 코드·문서·설정·DB 스키마를 바꾸면 그날의 작업 기록(`docs/history/`)을 남깁니다(CLAUDE.md). 작업 기록에는 운영 환경 정보가 들어 있어 공개 저장소에는 올리지 않습니다. 공개된 변경 내용은 [CHANGELOG.md](CHANGELOG.md)에 있습니다.
- 설계 결정은 [ADR](docs/adr/README.md)로 남깁니다.
- 시스템 DB 스키마는 `db/schema.sql`을 재실행해도 안전하게 고친 뒤 `nlxpg db init`으로 적용합니다.
- 사내 문서, 업로드 파일, 산출물은 `data/private/`와 `out/`에 두고 커밋하지 않습니다.

```
nlxpg/
├── llm/          구조화 출력 LLM (vLLM, watsonx, Claude), 프로필(DB), 디버그 기록
├── parsing/      문서 로드, 섹션 분할
├── extraction/   추출 JSON Schema, 프롬프트, 추출기
├── reconcile/    문서 간 엔터티 정합 (현재: 이름·별칭 일치)
├── modeling/     명명, 타입, PK·FK, N:M
├── standards/    공통표준 로더, 표준 적용, 표준 검증
├── generate/     DDL, Mermaid ERD, 정의서
├── validate/     린트, 샌드박스, 정규형
├── evaluation/   정답 스키마 매칭 지표 (ADR-0003)
├── api/          웹 UI (FastAPI + static/index.html)
├── pipeline.py   단계 연결 · service.py 실행 1회 · store.py 시스템 DB · cli.py
db/schema.sql     시스템 DB 스키마
data/standards/   공통표준 CSV (2025-11-01 판) · data/samples/ 공개 샘플 문서
deploy/           install.sh, nlxpg.service
docs/             overview, user-guide(매뉴얼), plan(기획서), install, release, adr/
```

## 문서

| 문서 | 내용 |
| --- | --- |
| [docs/overview.md](docs/overview.md) | 소개: 무엇을, 어떻게, 보안, 현재 수준과 한계 |
| [docs/user-guide.md](docs/user-guide.md) | 사용자 매뉴얼: 화면별 사용법, 한빛항공사 따라 하기 예제, 관리자 기능, 문제 해결 |
| [docs/nlxpg-0.5-소개.pptx](docs/nlxpg-0.5-소개.pptx) | 소개 발표 자료 (10장, 소개 문서와 같은 내용) |
| [docs/plan.md](docs/plan.md) | 기획서: 목표, 아키텍처, 평가, 로드맵, 보안 기준 |
| [docs/install.md](docs/install.md) | 설치·운영·백업·문제 해결 |
| [docs/release.md](docs/release.md) | 배포 절차 |
| [docs/adr/](docs/adr/README.md) | 설계 결정 기록 |
| [CHANGELOG.md](CHANGELOG.md) | 버전별 변경 내용 |
| [docs/reference/public-db-standard-manual-notes.md](docs/reference/public-db-standard-manual-notes.md) | 공공데이터베이스 표준화 관리 매뉴얼 정리 |

## 라이선스

[Apache License 2.0](LICENSE). 함께 들어 있는 서드파티 소프트웨어(Mermaid)와 공공데이터(공통표준)의 라이선스·출처는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)에 있습니다.
