"""One place that turns configuration into model providers.

Effective model settings = `.env` (via `app.config`) + runtime overrides stored in
the `settings` table under `models.overrides`. Overrides are deliberately narrow:

- **allowed**: provider, model, temperature, budgets, prices, thresholds;
- **env only**: base URLs and API keys. A runtime PATCH that could redirect the
  configured key to another host would turn a local, unauthenticated API into a
  key-exfiltration endpoint, so the schema forbids those fields outright;
- **read-only**: embedding dimensions — a width change is a re-embed, guarded at
  startup (P12), not a setting.

Swapping provider is therefore configuration only: change `LLM_PROVIDER` /
`EMBEDDING_PROVIDER` (or PATCH them) and the next call uses the new provider.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, settings
from app.db.models import EMBEDDING_DIMENSIONS, Setting
from app.logging import get_logger
from app.models.embeddings.base import EmbeddingError, EmbeddingProvider
from app.models.embeddings.onnx_local import OnnxLocalEmbeddingProvider
from app.models.embeddings.remote import RemoteEmbeddingProvider
from app.models.embeddings.stub import StubEmbeddingProvider
from app.models.llm.base import ChatMessage, LLMError, LLMProvider, Pricing
from app.models.llm.openai_compatible import OpenAICompatibleProvider
from app.models.llm.stub import STUB_MODEL, StubLLMProvider, StubPolicy
from app.models.llm.zenmux import ZenMuxProvider

log = get_logger(__name__)

OVERRIDES_KEY = "models.overrides"
LLMProviderName = Literal["stub", "zenmux", "openai_compatible"]
EmbeddingProviderName = Literal["onnx_local", "remote", "stub"]
LLM_PROVIDERS: tuple[str, ...] = ("stub", "zenmux", "openai_compatible")
EMBEDDING_PROVIDERS: tuple[str, ...] = ("onnx_local", "remote", "stub")


# --- effective configuration ------------------------------------------------
class LLMConfig(BaseModel):
    provider: LLMProviderName
    base_url: str
    model: str
    temperature: float = Field(ge=0, le=2)
    max_tool_calls: int = Field(ge=1, le=50)
    timeout_s: int = Field(ge=5, le=600)
    retries: int = Field(ge=0, le=5)
    max_tokens: int = Field(ge=64, le=16000)
    json_mode: bool
    price_input_per_m: float = Field(ge=0)
    price_output_per_m: float = Field(ge=0)
    daily_budget_usd: float = Field(ge=0)


class EmbeddingConfig(BaseModel):
    provider: EmbeddingProviderName
    model: str
    dimensions: int
    base_url: str
    onnx_repo: str
    onnx_file: str


class AgentConfig(BaseModel):
    confidence_threshold: float = Field(ge=0, le=1)
    wall_clock_s: int = Field(ge=10, le=900)
    auto_remediate: bool


class RetrievalConfig(BaseModel):
    top_k: int = Field(ge=1, le=20)
    min_score: float = Field(ge=0, le=1)


class ModelConfig(BaseModel):
    llm: LLMConfig
    embedding: EmbeddingConfig
    agent: AgentConfig
    retrieval: RetrievalConfig


# --- PATCH schema (extra=forbid: base_url / api_key are rejected with 422) ---
class _Patch(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LLMPatch(_Patch):
    provider: LLMProviderName | None = None
    model: str | None = Field(default=None, min_length=1, max_length=128)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tool_calls: int | None = Field(default=None, ge=1, le=50)
    timeout_s: int | None = Field(default=None, ge=5, le=600)
    max_tokens: int | None = Field(default=None, ge=64, le=16000)
    json_mode: bool | None = None
    price_input_per_m: float | None = Field(default=None, ge=0)
    price_output_per_m: float | None = Field(default=None, ge=0)
    daily_budget_usd: float | None = Field(default=None, ge=0)


class EmbeddingPatch(_Patch):
    provider: EmbeddingProviderName | None = None
    model: str | None = Field(default=None, min_length=1, max_length=128)


class AgentPatch(_Patch):
    confidence_threshold: float | None = Field(default=None, ge=0, le=1)
    wall_clock_s: int | None = Field(default=None, ge=10, le=900)


class RetrievalPatch(_Patch):
    top_k: int | None = Field(default=None, ge=1, le=20)
    min_score: float | None = Field(default=None, ge=0, le=1)


class ModelSettingsPatch(_Patch):
    llm: LLMPatch | None = None
    embedding: EmbeddingPatch | None = None
    agent: AgentPatch | None = None
    retrieval: RetrievalPatch | None = None
    # Drop every stored override and return to the .env values.
    reset: bool = False


def base_config(source: Settings) -> ModelConfig:
    return ModelConfig(
        llm=LLMConfig(
            provider=source.llm_provider,  # type: ignore[arg-type]
            base_url=source.llm_base_url,
            model=source.llm_model,
            temperature=source.llm_temperature,
            max_tool_calls=source.llm_max_tool_calls,
            timeout_s=source.llm_timeout_s,
            retries=source.llm_retries,
            max_tokens=source.llm_max_tokens,
            json_mode=source.llm_json_mode,
            price_input_per_m=source.llm_price_input_per_m,
            price_output_per_m=source.llm_price_output_per_m,
            daily_budget_usd=source.llm_daily_budget_usd,
        ),
        embedding=EmbeddingConfig(
            provider=source.embedding_provider,  # type: ignore[arg-type]
            model=source.embedding_model,
            dimensions=source.embedding_dimensions,
            base_url=source.embedding_base_url,
            onnx_repo=source.embedding_onnx_repo,
            onnx_file=source.embedding_onnx_file,
        ),
        agent=AgentConfig(
            confidence_threshold=source.agent_confidence_threshold,
            wall_clock_s=source.agent_wall_clock_s,
            auto_remediate=source.agent_auto_remediate,
        ),
        retrieval=RetrievalConfig(
            top_k=source.retrieval_top_k, min_score=source.retrieval_min_score
        ),
    )


def apply_overrides(base: ModelConfig, overrides: dict[str, Any]) -> ModelConfig:
    merged = base.model_dump()
    for section, values in overrides.items():
        if section in merged and isinstance(values, dict):
            merged[section].update({k: v for k, v in values.items() if v is not None})
    return ModelConfig.model_validate(merged)


def mask_secret(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    return "••••" + value[-4:] if len(value) >= 12 else "••••"


# --- views --------------------------------------------------------------------
class SecretStatus(BaseModel):
    configured: bool
    masked: str | None = None
    source: Literal["env"] = "env"


class ModelSettingsView(BaseModel):
    config: ModelConfig
    overrides: dict[str, Any]
    llm_api_key: SecretStatus
    embedding_api_key: SecretStatus
    llm_ready: bool
    embedding_ready: bool
    embedding_loaded: bool
    store_dimensions: int
    providers: dict[str, list[str]]
    updated_at: datetime | None = None


class ModelTestResult(BaseModel):
    target: Literal["llm", "embedding"]
    ok: bool
    provider: str
    model_requested: str
    model_reported: str | None = None
    latency_ms: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    dimensions: int | None = None
    detail: str
    error_kind: str | None = None


class ModelTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["llm", "embedding", "all"] = "all"


class ModelTestReport(BaseModel):
    results: list[ModelTestResult]
    tested_at: datetime


# --- gateway --------------------------------------------------------------------
class ModelGateway:
    def __init__(self, source: Settings = settings) -> None:
        self.source = source
        self.overrides: dict[str, Any] = {}
        self.updated_at: datetime | None = None
        self.config = base_config(source)
        self._llm: LLMProvider | None = None
        self._embedder: EmbeddingProvider | None = None
        self._stub_policy: StubPolicy | None = None
        self._lock = asyncio.Lock()

    # configuration ---------------------------------------------------------
    async def load(self, session: AsyncSession) -> None:
        row = await session.get(Setting, OVERRIDES_KEY)
        overrides = dict(row.value) if row else {}
        try:
            config = apply_overrides(base_config(self.source), overrides)
        except ValueError as exc:
            # A stored override that no longer validates (e.g. a provider that
            # was removed) must not keep the API from starting.
            log.warning("model_overrides_ignored", error=str(exc))
            overrides, config = {}, base_config(self.source)
        await self._swap(config, overrides, row.updated_at if row else None)

    async def update(self, session: AsyncSession, patch: ModelSettingsPatch) -> None:
        if patch.reset:
            overrides: dict[str, Any] = {}
        else:
            overrides = {k: dict(v) for k, v in self.overrides.items()}
            for section, values in patch.model_dump(exclude_none=True, exclude={"reset"}).items():
                overrides.setdefault(section, {}).update(values)
        config = apply_overrides(base_config(self.source), overrides)
        row = await session.get(Setting, OVERRIDES_KEY)
        if row is None:
            row = Setting(key=OVERRIDES_KEY, value=overrides)
            session.add(row)
        else:
            row.value = overrides
        await session.commit()
        await self._swap(config, overrides, datetime.now(UTC))
        log.info(
            "model_settings_updated",
            llm=f"{config.llm.provider}:{config.llm.model}",
            embedding=f"{config.embedding.provider}:{config.embedding.model}",
        )

    async def _swap(
        self, config: ModelConfig, overrides: dict[str, Any], updated_at: datetime | None
    ) -> None:
        async with self._lock:
            old_llm, old_embedder = self._llm, self._embedder
            llm_changed = config.llm != self.config.llm
            embed_changed = config.embedding != self.config.embedding
            self.config, self.overrides, self.updated_at = config, overrides, updated_at
            if llm_changed:
                self._llm = None
            if embed_changed:
                self._embedder = None
        if llm_changed and old_llm is not None:
            await old_llm.aclose()
        if embed_changed and old_embedder is not None:
            await old_embedder.aclose()

    def set_stub_policy(self, policy: StubPolicy | None) -> None:
        """The agent registers its deterministic reasoner for the stub provider."""
        self._stub_policy = policy
        if isinstance(self._llm, StubLLMProvider):
            self._llm.policy = policy

    # providers ---------------------------------------------------------------
    def build_llm(self, config: LLMConfig | None = None) -> LLMProvider:
        cfg = config or self.config.llm
        if cfg.provider == "stub":
            return StubLLMProvider(
                model=cfg.model if cfg.model.startswith("stub") else STUB_MODEL,
                policy=self._stub_policy,
            )
        kwargs: dict[str, Any] = {
            "model": cfg.model,
            "api_key": self.source.llm_api_key,
            "pricing": Pricing(
                input_per_m=cfg.price_input_per_m, output_per_m=cfg.price_output_per_m
            ),
            "temperature": cfg.temperature,
            "timeout_s": float(cfg.timeout_s),
            "retries": cfg.retries,
            "max_tokens": cfg.max_tokens,
        }
        if cfg.provider == "zenmux":
            return ZenMuxProvider(base_url=cfg.base_url, **kwargs)
        return OpenAICompatibleProvider(base_url=cfg.base_url, **kwargs)

    def build_embedder(self, config: EmbeddingConfig | None = None) -> EmbeddingProvider:
        cfg = config or self.config.embedding
        if cfg.dimensions != EMBEDDING_DIMENSIONS:
            raise EmbeddingError(
                "dimension_mismatch",
                f"EMBEDDING_DIMENSIONS={cfg.dimensions} but memories.embedding is "
                f"vector({EMBEDDING_DIMENSIONS}); migrate and re-embed first",
            )
        if cfg.provider == "stub":
            return StubEmbeddingProvider(dimensions=cfg.dimensions)
        if cfg.provider == "remote":
            return RemoteEmbeddingProvider(
                base_url=cfg.base_url,
                model=cfg.model,
                api_key=self.source.embedding_api_key,
                dimensions=cfg.dimensions,
            )
        return OnnxLocalEmbeddingProvider(
            model=cfg.model,
            dimensions=cfg.dimensions,
            repo=cfg.onnx_repo,
            onnx_file=cfg.onnx_file,
            cache_dir=self.source.embedding_cache_dir,
            allow_download=self.source.embedding_allow_download,
            max_tokens=self.source.embedding_max_tokens,
            batch_size=self.source.embedding_batch_size,
            threads=self.source.embedding_threads,
        )

    def llm(self) -> LLMProvider:
        if self._llm is None:
            self._llm = self.build_llm()
        return self._llm

    def embedder(self) -> EmbeddingProvider:
        if self._embedder is None:
            self._embedder = self.build_embedder()
        return self._embedder

    @property
    def llm_ready(self) -> bool:
        return self.config.llm.provider == "stub" or bool(self.source.llm_api_key.strip())

    @property
    def embedding_ready(self) -> bool:
        provider = self.config.embedding.provider
        if provider == "remote":
            return bool(self.source.embedding_api_key.strip() and self.config.embedding.base_url)
        return True

    def view(self) -> ModelSettingsView:
        return ModelSettingsView(
            config=self.config,
            overrides=self.overrides,
            llm_api_key=SecretStatus(
                configured=bool(self.source.llm_api_key.strip()),
                masked=mask_secret(self.source.llm_api_key),
            ),
            embedding_api_key=SecretStatus(
                configured=bool(self.source.embedding_api_key.strip()),
                masked=mask_secret(self.source.embedding_api_key),
            ),
            llm_ready=self.llm_ready,
            embedding_ready=self.embedding_ready,
            embedding_loaded=bool(self._embedder is not None and self._embedder.loaded),
            store_dimensions=EMBEDDING_DIMENSIONS,
            providers={"llm": list(LLM_PROVIDERS), "embedding": list(EMBEDDING_PROVIDERS)},
            updated_at=self.updated_at,
        )

    # smoke tests -------------------------------------------------------------
    async def test_llm(self) -> ModelTestResult:
        cfg = self.config.llm
        provider = self.llm()
        started = time.perf_counter()
        try:
            response = await provider.chat(
                [
                    ChatMessage(role="system", content="You are a connectivity check."),
                    ChatMessage(role="user", content="Reply with the single word: ok"),
                ],
                max_tokens=64,
                temperature=0,
                timeout_s=min(30.0, float(cfg.timeout_s)),
            )
        except LLMError as exc:
            return ModelTestResult(
                target="llm",
                ok=False,
                provider=provider.name,
                model_requested=cfg.model if cfg.provider != "stub" else provider.model,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                detail=exc.message,
                error_kind=exc.kind,
            )
        text = (response.content or "").strip()
        return ModelTestResult(
            target="llm",
            ok=True,
            provider=response.provider,
            model_requested=provider.model,
            model_reported=response.model,
            latency_ms=response.latency_ms,
            tokens_in=response.usage.tokens_in,
            tokens_out=response.usage.tokens_out,
            cost_usd=response.cost_usd,
            detail=f"replied {text[:60]!r}" if text else "replied with no text",
        )

    async def test_embedding(self) -> ModelTestResult:
        cfg = self.config.embedding
        started = time.perf_counter()
        try:
            provider = self.embedder()
            vectors = await provider.embed(
                ["payment service returns errors; checkout cannot place orders"], kind="document"
            )
            vector = vectors[0]
        except EmbeddingError as exc:
            return ModelTestResult(
                target="embedding",
                ok=False,
                provider=cfg.provider,
                model_requested=cfg.model,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                detail=exc.message,
                error_kind=exc.kind,
            )
        norm = sum(value * value for value in vector) ** 0.5
        return ModelTestResult(
            target="embedding",
            ok=len(vector) == EMBEDDING_DIMENSIONS,
            provider=provider.name,
            model_requested=cfg.model,
            model_reported=provider.model,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            dimensions=len(vector),
            detail=f"{len(vector)}-d vector, L2 norm {norm:.4f}",
        )

    async def test(self, target: str) -> ModelTestReport:
        results: list[ModelTestResult] = []
        if target in {"llm", "all"}:
            results.append(await self.test_llm())
        if target in {"embedding", "all"}:
            results.append(await self.test_embedding())
        return ModelTestReport(results=results, tested_at=datetime.now(UTC))

    async def aclose(self) -> None:
        if self._llm is not None:
            await self._llm.aclose()
        if self._embedder is not None:
            await self._embedder.aclose()
        self._llm = None
        self._embedder = None


gateway = ModelGateway()

__all__ = [
    "EMBEDDING_PROVIDERS",
    "LLM_PROVIDERS",
    "AgentConfig",
    "EmbeddingConfig",
    "LLMConfig",
    "ModelConfig",
    "ModelGateway",
    "ModelSettingsPatch",
    "ModelSettingsView",
    "ModelTestReport",
    "ModelTestRequest",
    "ModelTestResult",
    "RetrievalConfig",
    "apply_overrides",
    "base_config",
    "gateway",
    "mask_secret",
]
