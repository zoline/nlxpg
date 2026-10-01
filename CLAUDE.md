# nlxpg 작업 규칙

## 작업 기록 (필수)

코드·문서·설정·DB 스키마를 바꿀 때마다 `docs/history/YYYY-MM-DD.md`에 기록한다.
형식과 항목(무엇을, 왜, 결정, 검증, 남은 일)은 [docs/history/README.md](docs/history/README.md)를 따른다.
`docs/history/`는 운영 환경 정보(서버 주소 등)가 들어 있어 공개 저장소에 올리지 않는다(`.gitignore`). 공개할 변경 요약은 `CHANGELOG.md`에 적는다.
그날 첫 기록이면 파일을 만들고 README의 표에 한 줄을 추가한다. 작업을 마칠 때 "남은 일"을 갱신한다.

## 결정

설계 결정은 `docs/adr/`에 ADR로 남긴다(`0000-template.md` 복사). 결정 대기 목록은 `docs/adr/README.md`.

## 개발

- 가상환경: `.venv` (`uv pip install -p .venv -e '.[dev]'`)
- 확인: `.venv/bin/ruff check nlxpg tests && .venv/bin/pytest -q`
- 접속정보는 `../pgxnl/.env`를 폴백으로 읽는다. nlxpg 전용 값은 `nlxpg/.env`(커밋 금지)
- 시스템 DB 스키마를 바꾸면 `db/schema.sql`을 재실행 안전하게 고치고 `nlxpg db init`
- 사내 문서·업로드·산출물은 `data/private/`, `out/`에 두고 커밋하지 않는다
