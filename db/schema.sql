-- nlxpg 시스템 DB 스키마. `nlxpg db init`이 적용한다(여러 번 실행해도 안전하도록 IF NOT EXISTS).
--
-- 사내 문서(4단계 파일럿)의 원문·추출 결과·evidence는 원문과 같은 등급으로 관리한다
-- (plan.md §데이터 보안). 그래서 문서마다 보안 구분(classification)을 기록한다.

CREATE TABLE IF NOT EXISTS nlxpg_documents (
    doc_id          text PRIMARY KEY,
    title           text NOT NULL,
    source_path     text,
    media_type      text,
    content_sha256  text NOT NULL,
    classification  text NOT NULL DEFAULT 'public'
                    CHECK (classification IN ('public', 'internal')),
    created_at      timestamptz NOT NULL DEFAULT now()
);
-- 원문(마크다운). 검토 화면에서 근거(evidence)와 나란히 본다. 사내 문서면 원문과 같은 등급이다.
ALTER TABLE nlxpg_documents ADD COLUMN IF NOT EXISTS content text;

-- 파이프라인 1회 실행. 입력 문서 여러 개 → 스키마 하나.
CREATE TABLE IF NOT EXISTS nlxpg_runs (
    run_id          bigserial PRIMARY KEY,
    label           text,
    status          text NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'succeeded', 'failed')),
    llm_provider    text,
    llm_model       text,
    config          jsonb NOT NULL DEFAULT '{}'::jsonb,
    schema_ir       jsonb,            -- 스키마 중간 표현(nlxpg.ir.SchemaIR)
    ddl             text,
    ddl_valid       boolean,
    validation      jsonb,            -- 린트·샌드박스 결과
    error           text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz
);

CREATE TABLE IF NOT EXISTS nlxpg_run_documents (
    run_id  bigint NOT NULL REFERENCES nlxpg_runs (run_id) ON DELETE CASCADE,
    doc_id  text   NOT NULL REFERENCES nlxpg_documents (doc_id),
    PRIMARY KEY (run_id, doc_id)
);

-- 정답 스키마 대비 자동 지표 (ADR-0003).
CREATE TABLE IF NOT EXISTS nlxpg_eval_results (
    eval_id         bigserial PRIMARY KEY,
    run_id          bigint NOT NULL REFERENCES nlxpg_runs (run_id) ON DELETE CASCADE,
    dataset         text NOT NULL,     -- 'bird_reverse' | 'doc2db_bench' | 'squid' ...
    case_id         text NOT NULL,
    matcher         text NOT NULL,     -- 'exact' | 'embedding' | 'llm'
    metrics         jsonb NOT NULL,    -- table/column P·R·F1, pk/fk 일치율, 타입 정확도
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, dataset, case_id, matcher)
);

-- 사람 검토 체크리스트 (plan.md §사람 검토 절차). 항목당 1~5점.
CREATE TABLE IF NOT EXISTS nlxpg_reviews (
    review_id           bigserial PRIMARY KEY,
    run_id              bigint NOT NULL REFERENCES nlxpg_runs (run_id) ON DELETE CASCADE,
    reviewer            text NOT NULL,
    entity_completeness smallint CHECK (entity_completeness BETWEEN 1 AND 5),
    normalization       smallint CHECK (normalization BETWEEN 1 AND 5),
    keys_relationships  smallint CHECK (keys_relationships BETWEEN 1 AND 5),
    data_types          smallint CHECK (data_types BETWEEN 1 AND 5),
    naming              smallint CHECK (naming BETWEEN 1 AND 5),
    evidence            smallint CHECK (evidence BETWEEN 1 AND 5),
    comment             text,
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- ── 사용자 (2026-10-01) ─────────────────────────────────────────────
-- 역할: admin(사용자 관리·시스템 정보·모든 실행) / user(자기 실행만).
-- 비밀번호는 scrypt 해시만 저장한다(nlxpg/auth.py).
CREATE TABLE IF NOT EXISTS nlxpg_users (
    user_id              serial PRIMARY KEY,
    username             text NOT NULL UNIQUE,
    display_name         text NOT NULL,
    email                text,
    role                 text NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    password_hash        text NOT NULL,
    enabled              boolean NOT NULL DEFAULT true,
    must_change_password boolean NOT NULL DEFAULT true,
    created_at           timestamptz NOT NULL DEFAULT now(),
    last_login_at        timestamptz
);

-- 로그인 세션. 쿠키에는 무작위 토큰을 주고 DB에는 그 해시만 둔다.
CREATE TABLE IF NOT EXISTS nlxpg_sessions (
    token_hash   text PRIMARY KEY,
    user_id      integer NOT NULL REFERENCES nlxpg_users (user_id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL
);

-- 실행을 만든 사용자. 사용자 도입 전 실행은 NULL(관리자만 본다).
ALTER TABLE nlxpg_runs ADD COLUMN IF NOT EXISTS created_by integer
    REFERENCES nlxpg_users (user_id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS nlxpg_runs_created_by ON nlxpg_runs (created_by);

-- ── LLM 프로필 (2026-10-01) ─────────────────────────────────────────
-- 관리 → 모델 설정 화면에서 등록한다. 키·비밀번호는 NLXPG_SECRET_KEY로 암호화(nlxpg/crypto.py).
CREATE TABLE IF NOT EXISTS nlxpg_llm_profiles (
    profile_id            serial PRIMARY KEY,
    name                  text NOT NULL UNIQUE,
    provider              text NOT NULL,
    model                 text NOT NULL,
    base_url              text NOT NULL,
    temperature           real NOT NULL DEFAULT 0.0,
    max_tokens            integer NOT NULL DEFAULT 8192,
    verify_ssl            boolean NOT NULL DEFAULT true,
    guided_mode           text CHECK (guided_mode IN ('json_schema', 'json_object', 'none')),
    api_key_enc           text,
    watsonx_project_id    text,
    watsonx_space_id      text,
    watsonx_username      text,
    watsonx_password_enc  text,
    watsonx_instance_id   text,
    watsonx_api_version   text NOT NULL DEFAULT '2024-05-01',
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);

-- 제공자. anthropic(Claude)은 외부 API라 사내 문서가 아닌 실행에만 쓴다(ADR-0008, 2026-10-01).
-- 이전 판의 CHECK(openai, watsonx)를 바꾸려고 지우고 다시 만든다(재실행 안전).
ALTER TABLE nlxpg_llm_profiles DROP CONSTRAINT IF EXISTS nlxpg_llm_profiles_provider_check;
ALTER TABLE nlxpg_llm_profiles ADD CONSTRAINT nlxpg_llm_profiles_provider_check
    CHECK (provider IN ('openai', 'watsonx', 'anthropic'));

-- 역할별로 쓸 프로필. 행이 없으면 .env 설정을 쓴다.
CREATE TABLE IF NOT EXISTS nlxpg_llm_roles (
    role              text PRIMARY KEY CHECK (role IN ('extract', 'testdoc')),
    profile_id        integer NOT NULL REFERENCES nlxpg_llm_profiles (profile_id) ON DELETE CASCADE,
    -- Qwen3 등 reasoning 모델의 사고 과정 끄기(vLLM chat_template_kwargs.enable_thinking=false)
    disable_thinking  boolean NOT NULL DEFAULT false
);

-- ── LLM 호출 기록 (디버그 모드, 2026-10-01) ─────────────────────────
-- 새 설계에서 "디버그 모드"를 켠 실행만 남긴다. 프롬프트에 원문 섹션이 들어가므로
-- 사내 문서 실행이면 원문과 같은 등급이다. 실행을 지우면 함께 지워진다.
CREATE TABLE IF NOT EXISTS nlxpg_llm_calls (
    call_id        bigserial PRIMARY KEY,
    run_id         bigint NOT NULL REFERENCES nlxpg_runs (run_id) ON DELETE CASCADE,
    label          text,              -- 문서 › 섹션 경로
    attempt        smallint NOT NULL DEFAULT 0,   -- 0: 첫 요청, 1~: 검증 실패 후 수정 요청
    schema_name    text,
    model          text,
    system_prompt  text,
    messages       jsonb,
    response       text,
    finish_reason  text,
    input_tokens   integer,
    output_tokens  integer,
    duration_ms    integer,
    error          text,              -- 호출 오류 또는 스키마 검증 실패
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS nlxpg_llm_calls_run ON nlxpg_llm_calls (run_id, call_id);
