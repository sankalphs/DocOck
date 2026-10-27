"""P10: LLM adapter, stub providers, embeddings, and the model gateway."""

from __future__ import annotations

import json
import math
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import app
from app.models import gateway as gateway_module
from app.models.embeddings.base import EmbeddingError
from app.models.embeddings.stub import StubEmbeddingProvider
from app.models.gateway import (
    ModelGateway,
    ModelSettingsPatch,
    apply_overrides,
    base_config,
    mask_secret,
)
from app.models.llm import openai_compatible
from app.models.llm.base import ChatMessage, LLMError, Pricing, ToolCall, ToolSpec
from app.models.llm.openai_compatible import OpenAICompatibleProvider
from app.models.llm.stub import StubLLMProvider
from app.models.llm.zenmux import ZenMuxProvider

SECRET = "sk-test-0123456789abcdef"


def _completion(**message: Any) -> dict[str, Any]:
    return {
        "id": "cmpl-1",
        "model": "meta/muse-spark-1.3-contributor-2026",
        "choices": [
            {"index": 0, "message": {"role": "assistant", **message}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
    }


def _provider(handler: Any, **kwargs: Any) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url="https://llm.test/v1",
        model="meta/muse-spark-1.3-contributor",
        api_key=SECRET,
        pricing=Pricing(input_per_m=0.10, output_per_m=0.20),
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(openai_compatible.asyncio, "sleep", _sleep)


async def test_tool_call_usage_cost_and_reported_model() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=_completion(
                content=None,
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_topology", "arguments": '{"window": "5m"}'},
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "get_logs", "arguments": "{not json"},
                    },
                ],
            ),
        )

    provider = _provider(handler)
    tool = ToolSpec(name="get_topology", description="edges", parameters={"type": "object"})
    response = await provider.chat(
        [ChatMessage(role="user", content="investigate")], tools=[tool], json_mode=True
    )
    assert seen["auth"] == f"Bearer {SECRET}"
    assert seen["body"]["tools"][0]["function"]["name"] == "get_topology"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert [call.name for call in response.tool_calls] == ["get_topology", "get_logs"]
    assert response.tool_calls[0].arguments == {"window": "5m"}
    # Invalid JSON arguments are surfaced, not guessed.
    assert response.tool_calls[1].arguments is None
    assert response.usage.tokens_in == 1000 and response.usage.tokens_out == 500
    assert response.cost_usd == pytest.approx(0.0002)
    assert response.model == "meta/muse-spark-1.3-contributor-2026"
    assert response.latency_ms >= 0


async def test_retries_rate_limit_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json=_completion(content="ok"))

    response = await _provider(handler, retries=2).chat([ChatMessage(role="user", content="hi")])
    assert response.content == "ok"
    assert response.attempts == 3


async def test_exhausted_retries_raise_typed_error_without_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "overloaded"}})

    with pytest.raises(LLMError) as caught:
        await _provider(handler, retries=1).chat([ChatMessage(role="user", content="hi")])
    assert caught.value.kind == "upstream"
    assert SECRET not in str(caught.value)


async def test_auth_error_is_not_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    with pytest.raises(LLMError) as caught:
        await _provider(handler, retries=3).chat([ChatMessage(role="user", content="hi")])
    assert caught.value.kind == "auth"
    assert calls["n"] == 1


async def test_timeout_is_typed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMError) as caught:
        await _provider(handler, retries=1).chat([ChatMessage(role="user", content="hi")])
    assert caught.value.kind == "timeout"


async def test_json_mode_dropped_when_server_rejects_response_format() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "response_format unsupported"}})
        return httpx.Response(200, json=_completion(content='{"ok": true}'))

    response = await _provider(handler, retries=0).chat(
        [ChatMessage(role="user", content="hi")], json_mode=True
    )
    assert response.content == '{"ok": true}'
    assert "response_format" not in bodies[-1]


async def test_unconfigured_provider_refuses_without_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not be called")

    provider = OpenAICompatibleProvider(
        base_url="https://llm.test/v1",
        model="m",
        api_key="",
        transport=httpx.MockTransport(handler),
    )
    assert not provider.configured
    with pytest.raises(LLMError) as caught:
        await provider.chat([ChatMessage(role="user", content="hi")])
    assert caught.value.kind == "unconfigured"


async def test_stub_script_policy_and_default() -> None:
    stub = StubLLMProvider(
        script=[ToolCall(id="c1", name="get_topology", raw_arguments="{}", arguments={})]
    )
    first = await stub.chat([ChatMessage(role="user", content="x" * 400)])
    assert first.tool_calls[0].name == "get_topology"
    assert first.usage.tokens_in == 100
    assert first.cost_usd == 0
    second = await stub.chat([ChatMessage(role="user", content="hi")], json_mode=True)
    assert json.loads(second.content or "") == {"ok": True}
    stub.policy = lambda messages, tools, json_mode: "policy answer"
    assert (await stub.chat([ChatMessage(role="user", content="hi")])).content == "policy answer"


async def test_stub_embeddings_are_1024d_normalised_and_lexical() -> None:
    embedder = StubEmbeddingProvider(dimensions=1024)
    a, b, c = await embedder.embed(
        [
            "payment application_error checkout errors",
            "payment application_error charge failures at checkout",
            "email container stopped",
        ]
    )
    assert len(a) == 1024
    assert math.isclose(sum(v * v for v in a), 1.0, rel_tol=1e-9)
    assert a == (await embedder.embed(["payment application_error checkout errors"]))[0]

    def cos(x: list[float], y: list[float]) -> float:
        return sum(p * q for p, q in zip(x, y, strict=True))

    assert cos(a, b) > cos(a, c)


def test_embedding_dimension_guard() -> None:
    embedder = StubEmbeddingProvider(dimensions=768)
    with pytest.raises(EmbeddingError):
        embedder.check([[0.1] * 768], 1024)
    gw = ModelGateway(Settings(embedding_provider="stub", embedding_dimensions=768))
    with pytest.raises(EmbeddingError) as caught:
        gw.build_embedder()
    assert caught.value.kind == "dimension_mismatch"


def test_provider_swap_is_configuration_only() -> None:
    stub = ModelGateway(Settings(llm_provider="stub", llm_api_key=""))
    assert isinstance(stub.llm(), StubLLMProvider)
    assert stub.llm_ready

    zen = ModelGateway(Settings(llm_provider="zenmux", llm_api_key=SECRET))
    provider = zen.llm()
    assert isinstance(provider, ZenMuxProvider)
    assert provider.configured and zen.llm_ready

    generic = ModelGateway(
        Settings(
            llm_provider="openai_compatible",
            llm_base_url="http://127.0.0.1:1234/v1",
            llm_api_key="",  # not the developer's .env key
        )
    )
    assert type(generic.llm()) is OpenAICompatibleProvider
    assert not generic.llm_ready  # no key: reported, not faked

    overridden = apply_overrides(
        base_config(Settings(llm_provider="stub")),
        {"llm": {"provider": "zenmux", "model": "openai/gpt-x"}},
    )
    assert overridden.llm.provider == "zenmux" and overridden.llm.model == "openai/gpt-x"


def test_patch_rejects_secrets_and_base_urls() -> None:
    for body in (
        {"llm": {"api_key": "sk-evil"}},
        {"llm": {"base_url": "https://attacker.example/v1"}},
        {"embedding": {"dimensions": 768}},
        {"llm": {"provider": "not-a-provider"}},
    ):
        with pytest.raises(ValidationError):
            ModelSettingsPatch.model_validate(body)
    assert ModelSettingsPatch.model_validate({"llm": {"provider": "stub"}}).llm is not None


def test_mask_secret_never_returns_the_key() -> None:
    assert mask_secret("") is None
    assert mask_secret(SECRET) == "••••cdef"
    assert mask_secret("short") == "••••"


def test_settings_api_view_and_connection_test(monkeypatch: pytest.MonkeyPatch) -> None:
    gw = ModelGateway(Settings(llm_provider="stub", embedding_provider="stub", llm_api_key=SECRET))
    monkeypatch.setattr(gateway_module, "gateway", gw)
    from app.api import settings as settings_route

    monkeypatch.setattr(settings_route, "gateway", gw)
    client = TestClient(app)

    view = client.get("/api/settings/models")
    assert view.status_code == 200
    body = view.json()
    assert body["config"]["llm"]["provider"] == "stub"
    assert body["llm_api_key"] == {"configured": True, "masked": "••••cdef", "source": "env"}
    assert SECRET not in view.text
    assert body["store_dimensions"] == 1024

    report = client.post("/api/settings/models/test", json={"target": "all"})
    assert report.status_code == 200
    results = {item["target"]: item for item in report.json()["results"]}
    assert results["llm"]["ok"] and results["llm"]["model_reported"] == "stub-deterministic-v1"
    assert results["llm"]["latency_ms"] is not None
    assert results["embedding"]["ok"] and results["embedding"]["dimensions"] == 1024

    rejected = client.patch("/api/settings/models", json={"llm": {"api_key": "sk-evil"}})
    assert rejected.status_code == 422


async def test_connection_test_reports_failure_kind() -> None:
    gw = ModelGateway(Settings(llm_provider="zenmux", llm_api_key="", embedding_provider="stub"))
    result = await gw.test_llm()
    assert not result.ok
    assert result.error_kind == "unconfigured"
    assert result.provider == "zenmux"


@pytest.mark.live
async def test_onnx_local_returns_1024d_on_cpu() -> None:
    gw = ModelGateway(Settings(embedding_provider="onnx_local", embedding_allow_download=False))
    result = await gw.test_embedding()
    assert result.ok, result.detail
    assert result.dimensions == 1024
    query, doc_hit, doc_miss = await gw.embedder().embed(
        [
            "payment errors at checkout",
            "payment. application_error. charge failures at checkout",
            "email. dependency_unavailable. container stopped",
        ]
    )
    assert sum(a * b for a, b in zip(query, doc_hit, strict=True)) > sum(
        a * b for a, b in zip(query, doc_miss, strict=True)
    )
    await gw.aclose()
