#!/usr/bin/env bash
# nlxpg 설치 스크립트. 저장소 루트에서 실행한다. 여러 번 실행해도 안전하다.
#
#   deploy/install.sh                     # 파이썬 환경 + .env + 시스템 DB 스키마
#   deploy/install.sh --pg-local          # 이 서버의 PostgreSQL에 nlxpg 계정·DB도 만든다
#   deploy/install.sh --pg-local --service   # + systemd 서비스로 웹 UI 상시 실행
#   deploy/install.sh --pg-local --pg-admin postgres   # sudo 대신 postgres 비밀번호로 접속
#   deploy/install.sh --dry-run ...       # 바꾸는 명령을 실행하지 않고 보여주기만 한다
#   LLM은 설치 후 웹 UI(관리 → 모델 설정)에서 등록한다 — 설정은 시스템 DB에만 저장된다.
#   화면을 못 쓰는 서버는 --llm-* 옵션 + NLXPG_SETUP_SECRET(키), 또는 나중에 'nlxpg setup llm'.
#
# 자세한 설명: docs/install.md
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADMIN_OUT=""
ENV_FILE="$ROOT/.env"
VENV="$ROOT/.venv"

PG_LOCAL=0; SERVICE=0; DRY=0
PG_HOST="localhost"; PG_PORT="5432"; DB_NAME="nlxpg"; DB_USER="nlxpg"
WEB_PORT="8200"; ADMIN_USER="admin"; PG_VERSION=""; PG_ADMIN=""; EXTRAS=""
LLM_ARGS=()

usage() { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; cat <<'EOF'

옵션:
  --pg-local           이 서버의 PostgreSQL(sudo -u postgres)로 계정·DB를 만든다
  --pg-admin USER      sudo 대신 이 DB 관리자 계정(예: postgres)과 비밀번호로 접속한다.
                       비밀번호는 PGPASSWORD 환경변수, 없으면 숨김 입력으로 묻는다
  --pg-port N          PostgreSQL 포트 (기본 5432)
  --db-name NAME       시스템 DB 이름 (기본 nlxpg)
  --db-user NAME       시스템 DB 계정 (기본 nlxpg)
  --llm-provider P     LLM 제공자: openai(vLLM 등) | watsonx
  --llm-model M        LLM 모델
  --llm-url URL        LLM 주소
  --llm-no-verify      LLM 서버 인증서를 검증하지 않는다(자체서명)
                       키·비밀번호는 환경변수 NLXPG_SETUP_SECRET으로 넘긴다(명령줄에 쓰지 않는다)
  --service            systemd 서비스(nlxpg.service) 등록·시작 (sudo 필요)
  --web-port N         웹 UI 포트 (기본 8200)
  --with-claude        Claude(Anthropic API) 패키지도 설치한다 — 외부 API, 사내 문서가 아닌 실행에만 (ADR-0008)
  --dry-run            바꾸는 명령은 실행하지 않고 출력만 한다
  -h, --help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pg-local) PG_LOCAL=1 ;;
    --pg-port) PG_PORT="$2"; shift ;;
    --pg-admin) PG_ADMIN="$2"; shift ;;
    --db-name) DB_NAME="$2"; shift ;;
    --db-user) DB_USER="$2"; shift ;;
    --service) SERVICE=1 ;;
    --llm-provider) LLM_ARGS+=(--provider "$2"); shift ;;
    --llm-model) LLM_ARGS+=(--model "$2"); shift ;;
    --llm-url) LLM_ARGS+=(--base-url "$2"); shift ;;
    --llm-no-verify) LLM_ARGS+=(--no-verify-ssl) ;;
    --web-port) WEB_PORT="$2"; shift ;;
    --with-claude) EXTRAS="[claude]" ;;
    --dry-run) DRY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "모르는 옵션: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

say()  { printf '\n\033[1;34m▶ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
# 바꾸는 명령은 run으로 감싼다 (--dry-run이면 출력만)
run()  { if [[ $DRY -eq 1 ]]; then printf '  [dry-run] %s\n' "$*"; else "$@"; fi; }

# .env의 KEY=VALUE를 넣거나 바꾼다
set_env() {
  local key="$1" value="$2"
  if [[ $DRY -eq 1 ]]; then printf '  [dry-run] .env: %s=%s\n' "$key" "${3:-$value}"; return; fi
  if grep -qE "^${key}=" "$ENV_FILE"; then
    local tmp; tmp="$(mktemp)"
    awk -v k="$key" -v v="$value" -F= 'BEGIN{OFS="="} $1==k {print k"="v; next} {print}' "$ENV_FILE" > "$tmp"
    cat "$tmp" > "$ENV_FILE"; rm -f "$tmp"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}
get_env() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true; }

cd "$ROOT"
[[ -f pyproject.toml && -d nlxpg ]] || die "nlxpg 저장소 루트를 찾지 못했다: $ROOT"
[[ $DRY -eq 1 ]] && warn "dry-run: 아무것도 바꾸지 않는다"

# ── 1. 파이썬 ──────────────────────────────────────────
say "1. 파이썬 3.12 이상"
PY=""
# 다른 가상환경의 파이썬이 PATH 앞에 있어도 시스템 파이썬을 먼저 쓴다
for cand in /usr/bin/python3.13 /usr/bin/python3.12 /usr/bin/python3 python3.13 python3.12 python3; do
  if command -v "$cand" >/dev/null && "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
    PY="$(command -v "$cand")"; break
  fi
done
[[ -n $PY ]] || die "파이썬 3.12 이상이 필요하다 (Ubuntu: sudo apt install python3.12 python3.12-venv)"
ok "$PY ($("$PY" --version 2>&1))"

# ── 2. 가상환경과 패키지 ────────────────────────────────
say "2. 가상환경(.venv)과 nlxpg 패키지"
if [[ ! -x $VENV/bin/python ]]; then
  run "$PY" -m venv "$VENV" || die "venv를 만들지 못했다 (Ubuntu: sudo apt install python3.12-venv)"
fi
# 이미 Claude 패키지가 있으면 업그레이드 때도 유지한다
[[ -z $EXTRAS ]] && "$VENV/bin/python" -c "import anthropic" 2>/dev/null && EXTRAS="[claude]"
if command -v uv >/dev/null; then
  run uv pip install -q -p "$VENV" -e "$ROOT$EXTRAS"
else
  run "$VENV/bin/pip" install -q --upgrade pip
  run "$VENV/bin/pip" install -q -e "$ROOT$EXTRAS"
fi
[[ -n $EXTRAS ]] && ok "선택 설치: $EXTRAS"
[[ $DRY -eq 1 ]] || ok "$("$VENV/bin/nlxpg" --help >/dev/null && echo "nlxpg 명령 설치됨: $VENV/bin/nlxpg")"

# ── 3. 설정 파일 ───────────────────────────────────────
say "3. 설정 파일(.env)"
if [[ ! -f $ENV_FILE ]]; then
  run cp "$ROOT/.env.example" "$ENV_FILE"
  run chmod 600 "$ENV_FILE"
  ok ".env.example을 복사했다 — LLM 설정을 채워야 한다 (아래 6단계)"
else
  ok ".env가 이미 있다 (그대로 둔다)"
fi
# LLM 키·비밀번호 암호화 키 (nlxpg/crypto.py). 잃으면 LLM 키를 다시 입력해야 하니 .env를 백업한다.
if [[ -z $(get_env NLXPG_SECRET_KEY) ]]; then
  set_env NLXPG_SECRET_KEY "$("$PY" -c 'import secrets; print(secrets.token_urlsafe(32))')" "(무작위 생성)"
  ok "NLXPG_SECRET_KEY 생성"
fi

# ── 4. 로컬 PostgreSQL에 계정·DB ─────────────────────────
if [[ $PG_LOCAL -eq 1 ]]; then
  say "4. 로컬 PostgreSQL에 계정 '$DB_USER'·DB '$DB_NAME'"
  command -v psql >/dev/null || die "psql이 없다 (Ubuntu: sudo apt install postgresql)"
  if command -v pg_lsclusters >/dev/null; then
    PG_VERSION="$(pg_lsclusters -h 2>/dev/null | awk -v p="$PG_PORT" '$3==p {print $1"/"$2; exit}')"
    [[ -n $PG_VERSION ]] && ok "클러스터 $PG_VERSION (포트 $PG_PORT)"
  fi
  # 관리자 접속: 기본은 sudo -u postgres(peer 인증, 비밀번호 불필요), --pg-admin이면 TCP + 비밀번호
  if [[ -n $PG_ADMIN ]]; then
    if [[ -z ${PGPASSWORD:-} && $DRY -eq 0 ]]; then
      read -r -s -p "  $PG_ADMIN 비밀번호: " PGPASSWORD; echo
    fi
    export PGPASSWORD
    admin_psql() { psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_ADMIN" -d postgres "$@"; }
    ADMIN_DESC="psql -h $PG_HOST -p $PG_PORT -U $PG_ADMIN"
  else
    admin_psql() { sudo -u postgres psql -p "$PG_PORT" "$@"; }
    ADMIN_DESC="sudo -u postgres psql -p $PG_PORT"
  fi
  if [[ $DRY -eq 0 ]]; then
    admin_psql -X -tAc 'select 1' >/dev/null 2>&1 \
      || die "관리자로 접속하지 못했다 ($ADMIN_DESC). 권한·비밀번호·PostgreSQL 실행 상태를 확인한다"
    ok "관리자 접속 확인 ($ADMIN_DESC)"
  fi

  # 이미 .env에 이 DB의 접속 문자열이 있으면 그 비밀번호를 그대로 쓴다(재실행해도 비밀번호가 바뀌지 않게)
  EXISTING="$(get_env NLXPG_SYSTEM_PG_DSN)"
  DB_PASS=""
  if [[ $EXISTING == postgresql://"$DB_USER":*@*/"$DB_NAME"* ]]; then
    DB_PASS="$("$PY" -c 'import sys,urllib.parse as u; print(u.unquote(u.urlsplit(sys.argv[1]).password or ""))' "$EXISTING")"
  fi
  [[ -n $DB_PASS ]] || DB_PASS="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(18))')"

  # psql의 작은따옴표 안에서는 ' 는 '' 로, \ 는 \\ 로 써야 한다
  q() { local v="${1//\\/\\\\}"; printf '%s' "${v//\'/\'\'}"; }
  # 비밀번호는 명령줄 인자(ps에 보임)가 아니라 표준입력의 \set으로 넘긴다.
  # 식별자·리터럴은 format(%I, %L)로 인용하고, 없을 때만 만든다(\gexec).
  SQL=$(cat <<EOF
\\set ON_ERROR_STOP on
\\set u '$(q "$DB_USER")'
\\set d '$(q "$DB_NAME")'
\\set p '$(q "$DB_PASS")'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'u', :'p')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'u') \\gexec
SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :'u', :'p') \\gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'d', :'u')
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'d') \\gexec
EOF
)
  if [[ $DRY -eq 1 ]]; then
    printf '  [dry-run] %s  (CREATE ROLE %s / CREATE DATABASE %s OWNER %s, 없을 때만)\n' \
      "$ADMIN_DESC" "$DB_USER" "$DB_NAME" "$DB_USER"
  else
    printf '%s\n' "$SQL" | admin_psql -q -X >/dev/null
    ok "계정·DB 준비됨"
  fi
  set_env NLXPG_SYSTEM_PG_DSN "postgresql://$DB_USER:$DB_PASS@$PG_HOST:$PG_PORT/$DB_NAME" \
          "postgresql://$DB_USER:***@$PG_HOST:$PG_PORT/$DB_NAME"
  ok ".env에 NLXPG_SYSTEM_PG_DSN 기록"
else
  say "4. 로컬 PostgreSQL — 건너뜀 (--pg-local 없음)"
  [[ -n $(get_env NLXPG_SYSTEM_PG_DSN) ]] || warn "NLXPG_SYSTEM_PG_DSN이 비어 있다. 원격 DB면 .env에 직접 적거나 'nlxpg db bootstrap'을 쓴다"
fi

# ── 5. 시스템 DB 스키마와 첫 관리자 ──────────────────────
say "5. 시스템 DB 스키마와 첫 관리자"
if [[ $DRY -eq 1 ]]; then
  printf '  [dry-run] %s db init\n  [dry-run] %s users add %s --admin  (사용자가 없을 때만)\n' "$VENV/bin/nlxpg" "$VENV/bin/nlxpg" "$ADMIN_USER"
elif "$VENV/bin/nlxpg" db init; then
  if [[ -z $("$VENV/bin/nlxpg" users list 2>/dev/null) ]]; then
    ADMIN_OUT="$("$VENV/bin/nlxpg" users add "$ADMIN_USER" --name 관리자 --admin)"
    ok "$ADMIN_OUT"
  else
    ok "사용자가 이미 있다 (관리자를 새로 만들지 않는다)"
  fi
else
  warn "스키마를 적용하지 못했다. NLXPG_SYSTEM_PG_DSN을 확인하고 '$VENV/bin/nlxpg db init'을 다시 실행한다"
fi

# ── 6. LLM과 접속 확인 ─────────────────────────────────
say "6. LLM과 접속 확인"
if [[ $DRY -eq 1 ]]; then
  [[ ${#LLM_ARGS[@]} -gt 0 ]] && printf '  [dry-run] %s setup llm --if-missing %s\n' "$VENV/bin/nlxpg" "${LLM_ARGS[*]}"
  printf '  [dry-run] %s check\n' "$VENV/bin/nlxpg"
else
  # LLM은 DB에만 저장한다. 옵션을 준 자동 설치일 때만 여기서 등록하고, 아니면 웹 UI에서 한다.
  if [[ ${#LLM_ARGS[@]} -gt 0 ]]; then
    "$VENV/bin/nlxpg" setup llm --if-missing --no-test "${LLM_ARGS[@]}" </dev/null \
      || warn "LLM 프로필을 등록하지 못했다 — 웹 UI 관리 → 모델 설정에서 등록한다"
  fi
  "$VENV/bin/nlxpg" check || true
fi

# ── 7. systemd 서비스 ──────────────────────────────────
if [[ $SERVICE -eq 1 ]]; then
  say "7. systemd 서비스 nlxpg.service (포트 $WEB_PORT)"
  UNIT="$(sed -e "s#@USER@#$(id -un)#g" -e "s#@ROOT@#$ROOT#g" -e "s#@PORT@#$WEB_PORT#g" "$ROOT/deploy/nlxpg.service")"
  if [[ $DRY -eq 1 ]]; then
    printf '  [dry-run] /etc/systemd/system/nlxpg.service 작성:\n%s\n' "$(printf '%s\n' "$UNIT" | sed 's/^/    /')"
    printf '  [dry-run] sudo systemctl daemon-reload && sudo systemctl enable --now nlxpg\n'
  else
    printf '%s\n' "$UNIT" | sudo tee /etc/systemd/system/nlxpg.service >/dev/null
    sudo systemctl daemon-reload
    sudo systemctl enable --now nlxpg
    sleep 2
    systemctl is-active --quiet nlxpg && ok "실행 중 — 로그: journalctl -u nlxpg -f" || warn "시작 실패 — journalctl -u nlxpg -n 50"
  fi
else
  say "7. 서비스 — 건너뜀 (--service 없음). 직접 띄우기: $VENV/bin/nlxpg serve --port $WEB_PORT"
fi

say "완료"
echo "  웹 UI: http://$(hostname -I 2>/dev/null | awk '{print $1}'):$WEB_PORT"
echo "  LLM: 웹 UI에 관리자로 로그인 → 관리 → 모델 설정에서 등록 (처음 로그인하면 그 화면으로 안내한다)"
if [[ -n ${ADMIN_OUT:-} ]]; then
  echo "  로그인: 아이디 $ADMIN_USER, 위 5단계의 임시 비밀번호 (첫 로그인 때 바꾼다 — 이 출력 말고는 다시 볼 수 없다)"
fi
echo "  설정: $ENV_FILE   문서: docs/install.md"
