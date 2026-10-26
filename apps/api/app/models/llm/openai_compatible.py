"""OpenAI-compatible chat-completions adapter (ZenMux, OpenAI, vLLM, LM Studio, …).

Behaviour that matters:

- **Timeouts** are per request (`LLM_TIMEOUT_S`); the agent's wall clock is
  enforced one level up.
- **Retries**: transport errors, timeouts, 429 and 5xx are retried with
  exponential backoff (honouring `Retry-After`, capped); 4xx never are, except a
  400 that rejects `response_format`, which is retried once without JSON mode.
- **Accounting**: usage comes from the response; cost is computed locally from
  the configured per-million prices, so a provider that omits cost still has one.
- The API key is sent as a bearer header and never appears in an error message,
  a log line, or a returned object.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

import httpx

from app.logging import get_logger
from app.models.llm.base import (
    ChatMessage,
    ChatResponse,
    LLMError,
    LLMProvider,
    Pricing,
    ToolCall,
    ToolSpec,
    Usage,
    parse_arguments,
)

log = get_logger(__name__)

RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
MAX_BACKOFF_S = 8.0


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        pricing: Pricing | None = None,
        temperature: float = 0.1,
        timeout_s: float = 60.0,
        retries: int = 2,
        max_tokens: int = 1500,
        extra_headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key.strip()
        self.pricing = pricing or Pricing()
        self.temperature = temperature
        self.timeout_s = timeout_s
        self.retries = max(0, retries)
        self.max_tokens = max_tokens
        headers = {"User-Agent": "docock-api/0.1", **(extra_headers or {})}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(timeout_s, connect=min(10.0, timeout_s)),
            transport=transport,
        )

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _body(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None,
        json_mode: bool,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [message.to_wire() for message in messages],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if tools:
            body["tools"] = [tool.to_wire() for tool in tools]
            body["tool_choice"] = "auto"
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> ChatResponse:
        if not self.configured:
            raise LLMError("unconfigured", f"{self.name}: no API key configured (LLM_API_KEY)")
        body = self._body(
            messages,
            tools=tools,
            json_mode=json_mode,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        started = time.perf_counter()
        attempts = 0
        json_fallback_used = False
        last_error: LLMError | None = None
        while attempts <= self.retries:
            attempts += 1
            try:
                response = await self._client.post(
                    "/chat/completions",
                    json=body,
                    timeout=timeout_s or self.timeout_s,
                )
            except httpx.TimeoutException:
                last_error = LLMError("timeout", f"{self.name}: request timed out")
                await self._backoff(attempts, None)
                continue
            except httpx.HTTPError as exc:
                last_error = LLMError("upstream", f"{self.name}: {type(exc).__name__}")
                await self._backoff(attempts, None)
                continue

            if response.status_code == 200:
                return self._parse(response, started=started, attempts=attempts)

            detail = _error_detail(response)
            if (
                response.status_code == 400
                and "response_format" in body
                and not json_fallback_used
                and "response_format" in detail.lower()
            ):
                # Some OpenAI-compatible servers reject JSON mode; the agent
                # validates the JSON itself, so drop the hint and retry once.
                body.pop("response_format", None)
                json_fallback_used = True
                attempts -= 1
                continue
            if response.status_code in {401, 403}:
                raise LLMError(
                    "auth",
                    f"{self.name}: HTTP {response.status_code} {detail}",
                    status_code=response.status_code,
                )
            if response.status_code in RETRY_STATUS:
                kind = "rate_limited" if response.status_code == 429 else "upstream"
                last_error = LLMError(
                    kind,  # type: ignore[arg-type]
                    f"{self.name}: HTTP {response.status_code} {detail}",
                    status_code=response.status_code,
                )
                await self._backoff(attempts, response.headers.get("retry-after"))
                continue
            raise LLMError(
                "upstream",
                f"{self.name}: HTTP {response.status_code} {detail}",
                status_code=response.status_code,
            )
        assert last_error is not None
        log.warning("llm_failed", provider=self.name, model=self.model, kind=last_error.kind)
        raise last_error

    async def _backoff(self, attempt: int, retry_after: str | None) -> None:
        if attempt > self.retries:
            return
        delay = min(MAX_BACKOFF_S, 0.5 * 2 ** (attempt - 1))
        if retry_after:
            with contextlib.suppress(ValueError):
                delay = min(MAX_BACKOFF_S, max(delay, float(retry_after)))
        await asyncio.sleep(delay)

    def _parse(self, response: httpx.Response, *, started: float, attempts: int) -> ChatResponse:
        try:
            payload = response.json()
            choice = payload["choices"][0]
            message = choice.get("message") or {}
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("bad_response", f"{self.name}: malformed completion ({exc})") from exc

        calls: list[ToolCall] = []
        for index, raw in enumerate(message.get("tool_calls") or []):
            function = (raw or {}).get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, dict):
                arguments = json.dumps(arguments)
            calls.append(
                ToolCall(
                    id=str(raw.get("id") or f"call_{index}"),
                    name=str(function.get("name") or ""),
                    raw_arguments=arguments or "{}",
                    arguments=parse_arguments(arguments),
                )
            )
        usage_raw = payload.get("usage") or {}
        usage = Usage(
            tokens_in=int(usage_raw.get("prompt_tokens") or 0),
            tokens_out=int(usage_raw.get("completion_tokens") or 0),
        )
        content = message.get("content")
        if isinstance(content, list):
            # Some servers return content parts; keep only the text.
            content = "".join(
                str(part.get("text") or "") for part in content if isinstance(part, dict)
            )
        return ChatResponse(
            provider=self.name,
            model=str(payload.get("model") or self.model),
            content=content if isinstance(content, str) else None,
            tool_calls=calls,
            finish_reason=choice.get("finish_reason"),
            usage=usage,
            cost_usd=self.pricing.cost(usage),
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            attempts=attempts,
        )


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error)[:200]
    return str(error or payload)[:200]


__all__ = ["OpenAICompatibleProvider"]
