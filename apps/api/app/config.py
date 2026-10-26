"""Runtime configuration.

Every value has a sane local default; `.env` at the repository root overrides it.
Secrets live only here and in the environment — never in the database and never
in an API response.
"""

from __future__ import annotations

from pathlib import Path

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

    database_url: str = "postgresql+asyncpg://docock:docock@127.0.0.1:5433/docock"

    demo_frontend_url: str = "http://127.0.0.1:8080"
    prometheus_url: str = "http://127.0.0.1:9090"
    jaeger_url: str = "http://127.0.0.1:16686"
    jaeger_base_path: str = "/jaeger/ui"
    opensearch_url: str = "http://127.0.0.1:9200"
    opensearch_index: str = "otel-logs*"
    flagd_url: str = "http://127.0.0.1:8013"
    flagd_ofrep_url: str = "http://127.0.0.1:8016"
    demo_compose_project: str = "docock-demo"

    llm_provider: str = "zenmux"
    llm_base_url: str = "https://zenmux.ai/api/v1"
    llm_model: str = "meta/muse-spark-1.3-contributor"
    llm_api_key: str = ""
    llm_temperature: float = 0.1
    llm_max_tool_calls: int = 20
    llm_timeout_s: int = 90

    embedding_provider: str = "onnx_local"
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_dimensions: int = 1024

    agent_confidence_threshold: float = 0.70
    agent_auto_remediate: bool = False

    fault_default_ttl_s: int = 900
    fault_max_ttl_s: int = 3600


settings = Settings()
