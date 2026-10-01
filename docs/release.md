# 배포 절차

배포는 **git 저장소 + 태그** 단위로 합니다. 설치는 `git clone` 후 `deploy/install.sh`로 합니다. PyPI 패키지(wheel)로는 배포하지 않습니다. `db/schema.sql`, `data/standards/`, `deploy/`가 파이썬 패키지 밖에 있고 코드가 저장소 경로를 기준으로 읽기 때문입니다.

## 1. 배포 전 점검

| 항목 | 확인 방법 |
| --- | --- |
| 테스트 | `.venv/bin/ruff check nlxpg tests && .venv/bin/pytest -q` |
| 버전 | `pyproject.toml`의 `version`과 `nlxpg/__init__.py`의 `__version__`이 같음 |
| 변경 이력 | `CHANGELOG.md`에 이번 버전 항목이 있음 |
| 문서 | README, `docs/overview.md`의 "지금 수준"이 실제와 맞음 |
| 비밀·사내 자료 제외 | `git status --ignored`로 `.env`, `data/private/`, `out/`, `docs/reference/*.pdf`가 무시되는지 확인. `git grep -nI "postgresql://[^:]*:[^*@]"`로 비밀번호가 들어간 DSN이 없는지 확인 |
| 라이선스 | `THIRD_PARTY_NOTICES.md`에 새 의존성 반영 |
| 공개 저장소 | `docs/history/`가 무시되는지 확인(운영 환경 정보). 커밋 작성자 이메일은 GitHub 비공개 주소(`git config user.email`) |
| 새 서버 설치 시험 | 깨끗한 디렉터리에서 `git clone` → `deploy/install.sh --pg-local --dry-run` → 실제 설치 → 로그인 → 샘플 문서 설계 |

## 2. 태그와 배포

```bash
git add -A && git commit -m "Release 0.5.0"
git tag -a v0.5.0 -m "nlxpg 0.5.0"
git push origin main --tags                      # github.com/zoline/nlxpg (공개)
git archive --prefix=nlxpg-0.5.0/ -o nlxpg-0.5.0.tar.gz v0.5.0   # 인터넷 없는 서버용 묶음
```

## 3. 기존 서버 업그레이드

```bash
pg_dump -Fc -d "<시스템 DB DSN>" -f nlxpg_before_0.5.0.dump   # 백업 먼저
git fetch --tags && git checkout v0.5.0
deploy/install.sh                                # 패키지 재설치 + 스키마 갱신(재실행 안전)
sudo systemctl restart nlxpg
```

시스템 DB 스키마는 `db/schema.sql`에서 `IF NOT EXISTS`와 `DROP ... IF EXISTS` / `ADD`로 바뀌므로 업그레이드 때 따로 마이그레이션할 필요가 없습니다.

## 4. 다음 버전 번호

| 변경 | 버전 |
| --- | --- |
| 기능 추가, 호환되지 않는 변경 (1.0 전) | 0.6.0 |
| 버그 수정만 | 0.5.1 |
| 정량 평가 통과 후 사내 파일럿용 안정판 | 1.0.0 (기준은 기획서 성공 기준을 따름) |
