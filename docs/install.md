# 설치

다른 서버에 nlxpg를 설치하는 방법이다. 대상은 Ubuntu 22.04/24.04 서버 기준이다.

## 1. 준비물

| 항목 | 요구사항 |
| --- | --- |
| 파이썬 | 3.12 이상, `venv` 모듈 (`sudo apt install python3.12 python3.12-venv`) |
| PostgreSQL | 시스템 DB용. 12 이상. 같은 서버(로컬) 또는 원격 |
| LLM | OpenAI 호환 서버(vLLM 등) 또는 watsonx. 외부 API(Claude)는 사내 문서가 아닌 실행에만 선택적으로 쓴다 (ADR-0002, ADR-0008) |
| 네트워크 | 설치 때 PyPI 접속(패키지 설치). 실행 중에는 LLM 서버와 DB만 있으면 된다(웹 UI의 Mermaid는 저장소에 포함) |
| sudo | `--pg-local`(계정·DB 생성), `--service`(systemd 등록)를 쓸 때만 |

pgxnl은 없어도 된다. pgxnl과 같은 서버에 `../pgxnl/.env`가 있으면 비워 둔 설정을 거기서 가져온다.

## 2. 한 번에 설치 (권장)

```bash
git clone https://github.com/zoline/nlxpg.git && cd nlxpg
deploy/install.sh --pg-local --service
```

| 단계 | 하는 일 |
| --- | --- |
| 1 | 파이썬 3.12 이상 확인 (시스템 파이썬 우선) |
| 2 | `.venv` 생성, `pip install -e .` (`uv`가 있으면 `uv`로) |
| 3 | `.env`가 없으면 `.env.example`을 복사(권한 600), `NLXPG_SECRET_KEY`(LLM 키 암호화용) 생성 |
| 4 | `--pg-local`: `sudo -u postgres psql`(또는 `--pg-admin postgres`면 그 계정의 비밀번호)로 계정 `nlxpg`·DB `nlxpg`를 **없을 때만** 만들고, 무작위 비밀번호로 `.env`의 `NLXPG_SYSTEM_PG_DSN`을 채운다. `postgres` 비밀번호는 필요 없다(peer 인증). 다시 실행하면 `.env`의 기존 비밀번호를 그대로 쓴다 |
| 5 | `nlxpg db init`(시스템 DB 스키마), 사용자가 없으면 관리자 `admin` 생성 → **임시 비밀번호를 화면에 한 번 출력** |
| 6 | `nlxpg check`(DB 접속 확인). `--llm-*` 옵션을 준 자동 설치면 LLM 프로필도 DB에 등록한다 |
| 7 | `--service`: `/etc/systemd/system/nlxpg.service` 등록·시작 |

옵션: `--pg-admin USER`(sudo 대신 DB 관리자 비밀번호로 접속, 비밀번호는 `PGPASSWORD` 또는 숨김 입력), `--pg-port`, `--db-name`, `--db-user`, `--web-port`(기본 8200), `--with-claude`(Claude 패키지도 설치, ADR-0008. 한 번 설치하면 업그레이드 때도 유지), `--dry-run`(바꾸는 명령을 실행하지 않고 보여주기만). 여러 번 실행해도 안전하다.

화면 입력 없이 자동으로 설치할 때는 LLM 값을 옵션으로, 키·비밀번호는 환경변수로 넘긴다(명령줄에 쓰면 프로세스 목록에 보인다).

```bash
PGPASSWORD=<postgres 비밀번호> NLXPG_SETUP_SECRET=<LLM 키> deploy/install.sh --pg-local --pg-admin postgres \
  --llm-provider openai --llm-model qwen3.6-35b --llm-url https://<vllm-host>:8000/v1 --llm-no-verify
```

설치가 끝나면 웹 UI에 관리자로 로그인한다. 비밀번호를 바꾸면 **관리 → 모델 설정**으로 안내되고, 거기서 LLM을 등록한다(4절).

## 3. 시스템 DB를 원격 서버에 둘 때

`--pg-local` 대신 둘 중 하나를 쓴다.

```bash
# (가) DBA가 계정·DB를 만들어 준 경우: .env에 직접 적고 스키마만 적용
NLXPG_SYSTEM_PG_DSN=postgresql://nlxpg:<비밀번호>@<host>:5432/nlxpg
.venv/bin/nlxpg db init

# (나) 관리자 계정(postgres 등)을 알고 있는 경우: .env의 DSN에 적은 계정·DB를 만들어 준다
.venv/bin/nlxpg db bootstrap        # 관리자 DSN을 숨김 입력으로 묻는다(저장하지 않음)
```

비밀번호에 `#`, `$`, `@`, `/`, `:` 같은 특수문자가 있으면 DSN에서 URL 인코딩한다(`#` → `%23`, `$` → `%24`).

## 4. LLM 설정 (웹 UI)

LLM은 `.env`가 아니라 **시스템 DB**에 둔다. `.env`에는 시스템 DB 접속(`NLXPG_SYSTEM_PG_DSN`)과 암호화 키(`NLXPG_SECRET_KEY`)만 있으면 된다.

1. 웹 UI에 관리자로 로그인 → **관리 → 모델 설정** (LLM이 없으면 처음 로그인 때 이 화면으로 온다)
2. **프로필 추가**: 제공자(OpenAI 호환=vLLM 등 / watsonx), 모델, 주소, 인증서 검증, 키·비밀번호 → **저장하고 접속 시험**. 처음 만든 프로필은 "문서 추출" 역할에 자동으로 지정된다
3. 필요하면 **역할**에서 문서 추출·테스트 문서 작성에 쓸 프로필을 바꾸고, Qwen3 같은 reasoning 모델이면 "생각 끄기"를 켠다

| 제공자 | 주소 예 | 키 |
| --- | --- | --- |
| OpenAI 호환 (vLLM) | `https://<vllm-host>:8000/v1` | API 키(서버가 요구하면). 자체서명 인증서면 "인증서 검증" 끔 |
| watsonx (CPD) | `https://<cpd-host>/` | 프로젝트 ID, CPD 계정·비밀번호 (instance_id는 보통 `openshift`) |
| watsonx (IBM Cloud) | `https://us-south.ml.cloud.ibm.com` | 프로젝트 ID, API 키 (계정은 비움) |
| Claude (Anthropic, 외부 API) | `https://api.anthropic.com` | Anthropic API 키. 모델 예: `claude-opus-5-5`. 먼저 `deploy/install.sh --with-claude` (또는 `.venv/bin/pip install -e '.[claude]'`) |

- 키·비밀번호는 `NLXPG_SECRET_KEY`로 암호화해 저장하고 화면에 다시 보이지 않는다. **이 키를 잃으면 저장된 LLM 키를 다시 입력해야 한다** — `.env`를 백업한다.
- **Claude 프로필은 외부 API다**(ADR-0008). 문서 추출(기본) 역할에는 지정할 수 없다. 새 설계 화면의 "모델"에서 직접 고르고, "사내 문서"가 꺼져 있을 때만 실행된다. 공개 문서·테스트 문서로 온프렘 모델과 비교할 때 쓴다.
- 주소가 비어 있으면 호출하지 않는다(공개 OpenAI API로 나가지 않게, ADR-0002).
- 화면을 쓸 수 없는 서버: `.venv/bin/nlxpg setup llm` (같은 프로필을 DB에 만든다. `--role testdoc`도 가능)
- 첫 관리자 비밀번호를 미리 정하려면 설치 전에 `.env`에 `NLXPG_ADMIN_INITIAL_PASSWORD`를 적는다.
- 기관 추가 표준(ADR-0009) 예시를 넣으려면 `.venv/bin/nlxpg standards local import data/samples/기관표준_예시.csv` (또는 웹 UI 공통표준 → 기관 추가분 → CSV 불러오기). 선택 사항이다.

## 5. 실행과 관리

```bash
sudo systemctl restart nlxpg         # 재시작 (설정 변경 후)
sudo systemctl status nlxpg
journalctl -u nlxpg -f               # 로그

# 서비스 없이 직접 띄우기 (세션과 분리)
nohup setsid .venv/bin/nlxpg serve >> out/serve.log 2>&1 < /dev/null &   # >> 로 이어 쓴다(덮어쓰면 첫 관리자 비밀번호 로그를 잃는다)
pkill -f "[p]ython3 .venv/bin/nlxpg serve"   # 끄기. 패턴의 [p]는 이 명령을 실행하는 셸 자신을 죽이지 않기 위해서다
```

웹 UI: `http://<서버>:8200`. 관리자로 로그인해 **관리 → 사용자**에서 계정을 만든다. 첫 관리자 비밀번호는 설치 출력, 또는 서버가 만든 경우 `data/private/initial_admin_password.txt`(바꾸면 지워짐)에 있다.

```bash
.venv/bin/nlxpg users list           # 사용자
.venv/bin/nlxpg users passwd admin   # 관리자 비밀번호를 잊었을 때
.venv/bin/nlxpg runs list            # 실행 이력
```

## 6. 업그레이드

```bash
git pull
deploy/install.sh                    # 패키지 재설치 + 스키마 갱신(nlxpg db init, 재실행 안전)
sudo systemctl restart nlxpg
```

## 7. 백업과 복구

시스템 DB에는 실행 이력, 산출 스키마, 원문(사내 문서 포함), 검토 점수, 사용자가 있다. 업로드 원본은 `data/private/uploads/`에 있다.

```bash
pg_dump -Fc -d "$(grep ^NLXPG_SYSTEM_PG_DSN= .env | cut -d= -f2-)" -f nlxpg_$(date +%F).dump
tar czf nlxpg_uploads_$(date +%F).tgz data/private/uploads .env

pg_restore --clean -d "<DSN>" nlxpg_YYYY-MM-DD.dump     # 복구
```

사내 문서가 들어 있으면 백업 파일도 원문과 같은 등급으로 관리한다(plan.md §데이터 보안).

## 8. 제거

```bash
sudo systemctl disable --now nlxpg && sudo rm /etc/systemd/system/nlxpg.service && sudo systemctl daemon-reload
sudo -u postgres psql -c 'DROP DATABASE nlxpg' -c 'DROP ROLE nlxpg'     # 로컬 DB였다면
rm -rf <설치 디렉터리>
```

## 9. 문제 해결

| 증상 | 확인 |
| --- | --- |
| `nlxpg db init`에서 `password authentication failed` | `.env`의 DSN 비밀번호. 특수문자는 URL 인코딩 |
| `connection refused` / 시간 초과 | PostgreSQL 실행 상태(`pg_lsclusters`), 포트, 원격이면 `listen_addresses`와 방화벽 |
| `no pg_hba.conf entry` | 원격 접속이면 서버의 `pg_hba.conf`에 이 서버 주소를 허용하고 `sudo systemctl reload postgresql` |
| LLM `FAIL` / 설계 시작 시 "LLM이 설정되지 않았다" | 관리 → 모델 설정에서 프로필 등록·역할 지정, "접속 시험"으로 주소·인증서·키 확인 |
| "저장된 키를 복호화할 수 없다" | `NLXPG_SECRET_KEY`가 바뀌었다. 모델 설정에서 프로필의 키를 다시 입력한다 |
| 웹 UI에 로그인할 수 없다 | `nlxpg users list`로 계정 확인, `nlxpg users passwd <아이디>` |
| PDF·DOCX를 올리면 오류 | 문서 파싱 도구는 선택 설치다: `.venv/bin/pip install -e '.[parsing]'` (Docling, 용량이 크다) |
