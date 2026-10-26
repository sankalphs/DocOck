"""Runtime configuration.

Every value has a sane local default; `.env` at the repository root overrides it.
Secrets live only here and in the environment — never in the database and never
in an API response.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "DocOck"
    environment: str = "local"
    api_host: str = "127.0.0.1"
    api_port: int = 8001
    dashboard_port: int = 3001
    cors_origins: list[str] = [
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ]

    # --- Logging ------------------------------------------------------------
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # --- Database -----------------------------------------------------------
    database_url: str = "postgresql+asyncpg://docock:docock@127.0.0.1:5433/docock"
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 5

    # --- Telemetry endpoints (host-published ports) -------------------------
    demo_frontend_url: str = "http://127.0.0.1:8080"
    prometheus_url: str = "http://127.0.0.1:9090"
    jaeger_url: str = "http://127.0.0.1:16686"
    jaeger_base_path: str = "/jaeger/ui"
    opensearch_url: str = "http://127.0.0.1:9200"
    opensearch_index: str = "otel-logs*"
    flagd_url: str = "http://127.0.0.1:8013"
    flagd_ofrep_url: str = "http://127.0.0.1:8016"
    demo_compose_project: str = "docock-demo"

    # --- HTTP client / cache behaviour --------------------------------------
    http_timeout_s: float = 8.0
    http_retries: int = 2
    telemetry_cache_ttl_s: float = 10.0
    health_timeout_s: float = 5.0
    health_cache_ttl_s: float = 5.0

    # --- Reasoning model ----------------------------------------------------
    llm_provider: str = "zenmux"
    llm_base_url: str = "https://zenmux.ai/api/v1"
    llm_model: str = "meta/muse-spark-1.3-contributor"
    llm_api_key: str = ""
    llm_temperature: float = 0.1
    llm_max_tool_calls: int = 20
    llm_timeout_s: int = 90
    llm_daily_budget_usd: float = 2.0

    # --- Embeddings ---------------------------------------------------------
    embedding_provider: str = "onnx_local"
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_dimensions: int = 1024

    # --- Agent / detection --------------------------------------------------
    agent_confidence_threshold: float = 0.70
    agent_auto_remediate: bool = False
    agent_wall_clock_s: int = 90
    detector_interval_s: int = 15
    detector_consecutive_breaches: int = 3
    detector_error_rate_floor: float = 0.02
    detector_error_rate_multiplier: float = 3.0
    detector_latency_multiplier: float = 2.0
    detector_cooldown_s: int = 300

    # --- Faults -------------------------------------------------------------
    fault_default_ttl_s: int = 900
    fault_max_ttl_s: int = 3600

    @property
    def jaeger_api_url(self) -> str:
        return f"{self.jaeger_url.rstrip('/')}{self.jaeger_base_path.rstrip('/')}"

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key.strip())


settings = Settings()
