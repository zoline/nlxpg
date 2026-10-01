"""IBM watsonx.ai(Cloud Pak for Data 포함) 클라이언트.

인증·재시도 로직은 pgxnl/llm/watsonx_client.py에서 가져왔다. 그쪽 주석에 적힌 실측 사항:
- CPD 게이트웨이는 backend 5xx를 4xx 본문에 감싸 돌려줄 때가 있다 → 본문도 보고 재시도.
- 401은 재시도하지 않는다(틀린 비밀번호 반복 전송 시 LDAP 계정 잠금 위험).
- 동시 호출 시 토큰 발급을 직렬화하지 않으면 CPD가 동시 로그인을 401로 거부한다.
- 이 배포의 downstream vLLM 프록시는 요청에 백틱(`)이 있으면 결정론적으로 500을 낸다.

구조화 출력은 Text Chat API의 response_format={"type": "json_object"}까지만 쓴다.
스키마 자체는 프롬프트로 전달되고, 검증은 StructuredLLM.complete_json이 맡는다.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx

from nlxpg.llm.base import ChatResult, GuidedMode, LLMError, StructuredLLM

_IAM_TOKEN_URL = "https://iam.cloud.ibm.com/identity/token"
_CPD_TOKEN_TTL = 3600
_MAX_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.5
_GATEWAY_IN_BODY = re.compile(
    r"\b(?:50[0234])\b|bad\s*gateway|gateway\s*time|service\s*unavailable", re.IGNORECASE
)


def _is_retryable(status_code: int, body: str) -> bool:
    if status_code >= 500:
        return True
    return status_code >= 400 and status_code != 401 and bool(_GATEWAY_IN_BODY.search(body or ""))


def _summary(resp: httpx.Response, limit: int = 300) -> str:
    """오류 응답 본문 요약. 게이트웨이가 HTML 오류 페이지를 돌려주면 그대로 찍을 경우 수십 줄이
    된다(2026-10-01, CPD 503 'Application is not available'). 제목만 뽑고 길이를 자른다."""
    text = resp.text or ""
    if "<html" in text.lower():
        title = re.search(r"<h1[^>]*>(.*?)</h1>|<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        found = next((g for g in (title.groups() if title else ()) if g), "")
        return f"HTML 오류 페이지: {re.sub(r'\s+', ' ', found).strip() or '(제목 없음)'}"
    return text if len(text) <= limit else text[:limit] + " …"


def _strip_backticks(text: str) -> str:
    return text.replace("`", "'")


class WatsonxLLM(StructuredLLM):
    provider = "watsonx"

    def __init__(
        self,
        model: str,
        url: str,
        *,
        api_key: str = "",
        project_id: str | None = None,
        space_id: str | None = None,
        username: str | None = None,
        password: str | None = None,
        instance_id: str | None = None,
        guided_mode: GuidedMode = "json_object",
        temperature: float = 0.0,
        max_tokens: int = 8192,
        timeout: float = 300.0,
        api_version: str = "2024-05-01",
        verify_ssl: bool = True,
    ) -> None:
        # watsonx에는 json_schema 강제가 없어 json_object로 낮춘다.
        super().__init__("json_object" if guided_mode == "json_schema" else guided_mode)
        self.model = model
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._project_id = project_id
        self._space_id = space_id
        self._username = username
        self._password = password
        self._instance_id = instance_id
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._api_version = api_version
        self._http = httpx.AsyncClient(timeout=timeout, verify=verify_ssl)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    # ── 인증 ──────────────────────────────────────────
    def _token_valid(self) -> bool:
        return self._token is not None and time.time() < self._token_expires_at - 30

    async def _get_token(self) -> str:
        if self._token_valid():
            return self._token  # type: ignore[return-value]
        async with self._token_lock:
            if self._token_valid():
                return self._token  # type: ignore[return-value]
            if self._username:
                return await self._get_cpd_token()
            return await self._get_iam_token()

    async def _get_iam_token(self) -> str:
        resp = await self._post(
            _IAM_TOKEN_URL,
            data={"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": self._api_key},
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        )
        if resp.status_code >= 400:
            raise LLMError(f"watsonx IAM 토큰 발급 오류 {resp.status_code}: {_summary(resp)}")
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires_at = time.time() + data.get("expires_in", 3600)
        return self._token  # type: ignore[return-value]

    async def _get_cpd_token(self) -> str:
        body: dict[str, str] = {"username": self._username or ""}
        # api_key가 비밀번호보다 우선이다. pgxnl/.env 주석대로, 비밀번호 인증 계정이면
        # WATSONX_API_KEY를 비워 둬야 401을 피한다.
        if self._api_key:
            body["api_key"] = self._api_key
        elif self._password:
            body["password"] = self._password
        else:
            raise LLMError("watsonx CPD 인증에는 WATSONX_API_KEY 또는 WATSONX_PASSWORD가 필요하다")
        resp = await self._post(
            f"{self._url}/icp4d-api/v1/authorize",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json=body,
        )
        if resp.status_code >= 400:
            raise LLMError(f"watsonx CPD 토큰 발급 오류 {resp.status_code}: {_summary(resp)}")
        self._token = resp.json()["token"]
        self._token_expires_at = time.time() + _CPD_TOKEN_TTL
        return self._token  # type: ignore[return-value]

    async def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        """일시적 장애(연결 오류, 5xx, 본문에 게이트웨이 오류가 든 4xx)만 재시도한다."""
        for attempt in range(_MAX_RETRIES + 1):
            last = attempt == _MAX_RETRIES
            try:
                resp = await self._http.post(url, **kwargs)
            except httpx.HTTPError:
                if last:
                    raise
            else:
                if last or not _is_retryable(resp.status_code, resp.text):
                    return resp
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
        raise AssertionError("unreachable")

    # ── 호출 ──────────────────────────────────────────
    def _scope(self) -> dict[str, str]:
        if self._space_id:
            return {"space_id": self._space_id}
        if self._project_id:
            return {"project_id": self._project_id}
        raise LLMError("watsonx project_id 또는 space_id가 설정되어 있지 않다")

    def _params(self) -> dict[str, str]:
        params = {"version": self._api_version}
        if self._instance_id:
            params["instance_id"] = self._instance_id
        return params

    async def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        *,
        json_schema: dict[str, Any] | None = None,
        schema_name: str = "output",
    ) -> ChatResult:
        body: dict[str, Any] = {
            "model_id": self.model,
            "messages": [
                {"role": "system", "content": _strip_backticks(system_prompt)},
                *({**m, "content": _strip_backticks(m["content"])} for m in messages),
            ],
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            **self._scope(),
        }
        if json_schema is not None and self.guided_mode == "json_object":
            body["response_format"] = {"type": "json_object"}

        token = await self._get_token()
        resp = await self._post(
            f"{self._url}/ml/v1/text/chat",
            params=self._params(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
        )
        if resp.status_code >= 400:
            raise LLMError(f"watsonx API 오류 {resp.status_code}: {_summary(resp)}")
        data = resp.json()
        choice = data["choices"][0]
        raw_usage = data.get("usage") or {}
        usage = (
            {"input_tokens": raw_usage["prompt_tokens"], "output_tokens": raw_usage["completion_tokens"]}
            if "prompt_tokens" in raw_usage and "completion_tokens" in raw_usage else None
        )
        return ChatResult(choice["message"].get("content") or "", choice.get("finish_reason"), usage)

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        """채팅과 같은 토큰을 재사용한다 — CPD는 계정당 동시 세션을 제한한다(pgxnl 실측)."""
        token = await self._get_token()
        resp = await self._post(
            f"{self._url}/ml/v1/text/embeddings",
            params=self._params(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"model_id": model, "inputs": texts, **self._scope()},
        )
        if resp.status_code >= 400:
            raise LLMError(f"watsonx 임베딩 API 오류 {resp.status_code}: {_summary(resp)}")
        return [r["embedding"] for r in resp.json()["results"]]

    async def aclose(self) -> None:
        await self._http.aclose()
