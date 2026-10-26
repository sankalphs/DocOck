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
    demo_network: str = "opentelemetry-demo"
    load_generator_url: str = "http://127.0.0.1:8089"

    # --- HTTP client / cache behaviour --------------------------------------
    http_timeout_s: float = 8.0
    http_retries: int = 2
    telemetry_cache_ttl_s: float = 10.0
    health_timeout_s: float = 5.0
    health_cache_ttl_s: float = 5.0

    # --- Reasoning model ----------------------------------------------------
    # stub | zenmux | openai_compatible. The stub is the offline default: it
    # needs no key and no network, so tests and a key-less lab both work.
    # Swapping provider is configuration only (here or PATCH /api/settings/models).
    llm_provider: str = "stub"
    llm_base_url: str = "https://zenmux.ai/api/v1"
    llm_model: str = "meta/muse-spark-1.3-contributor"
    llm_api_key: str = ""
    llm_temperature: float = 0.1
    llm_max_tool_calls: int = 20
    # Per request; the whole investigation is bounded by AGENT_WALL_CLOCK_S.
    llm_timeout_s: int = 90
    llm_retries: int = 2
    llm_max_tokens: int = 1500
    llm_json_mode: bool = True
    # USD per million tokens (ADR 001: muse-spark $0.10 in / $0.20 out).
    llm_price_input_per_m: float = 0.10
    llm_price_output_per_m: float = 0.20
    llm_daily_budget_usd: float = 2.0

    # --- Embeddings ---------------------------------------------------------
    # onnx_local | remote | stub
    embedding_provider: str = "onnx_local"
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_dimensions: int = 1024
    embedding_onnx_repo: str = "onnx-community/Qwen3-Embedding-0.6B-ONNX"
    embedding_onnx_file: str = "onnx/model_int8.onnx"
    embedding_cache_dir: Path = ROOT_DIR / "runtime" / "models"
    embedding_allow_download: bool = True
    embedding_max_tokens: int = 512
    embedding_batch_size: int = 8
    embedding_threads: int = 4
    embedding_base_url: str = ""
    embedding_api_key: str = ""

    # --- Retrieval ----------------------------------------------------------
    retrieval_top_k: int = 5
    retrieval_min_score: float = 0.55

    # --- Agent / detection --------------------------------------------------
    agent_confidence_threshold: float = 0.70
    agent_auto_remediate: bool = False
    agent_wall_clock_s: int = 90

    # --- Remediation (P14) --------------------------------------------------
    # A gated diagnosis proposes its recommended action as a PENDING approval.
    # Nothing executes without a decision unless AGENT_AUTO_REMEDIATE=true and
    # the action is auto-eligible (low risk).
    remediation_auto_plan: bool = True
    approval_ttl_s: int = 600
    approval_token_ttl_s: int = 60
    remediation_sweep_interval_s: float = 5.0
    remediation_audit_file: Path = ROOT_DIR / "runtime" / "audit" / "actions.jsonl"

    # --- Verification (P15) -------------------------------------------------
    # An executed action passes when every criterion holds on this many
    # consecutive detector cycles. PLAN §9 says 90 s; flagd propagation (30-45 s)
    # plus the detector's 2-minute rate window make the clear time ~3 min, so
    # the timeout matches the scenario runner's resolve timeout.
    verification_clean_cycles: int = 4
    verification_timeout_s: int = 420

    detector_enabled: bool = True
    # The collector's span-metric flush is 10 s (P4), so 10 s is the finest
    # cadence that observes a new sample; three consecutive breaches still
    # debounce one noisy point.
    detector_interval_s: int = 10
    detector_window: str = "2m"
    detector_baseline_window_s: int = 900
    detector_consecutive_breaches: int = 3
    # Latency needs a longer debounce than errors: this demo's healthy p95 is
    # bimodal (checkout sits at ~20 ms median but hits the 15 s top bucket for a
    # minute at a time), so a 3-sample debounce opens false incidents. A real
    # slowdown lasts minutes; 9 samples is 90 s of a sustained breach.
    detector_latency_breaches: int = 9
    detector_recovery_samples: int = 4
    detector_min_errors: int = 2
    detector_warmup_s: int = 300
    detector_error_rate_floor: float = 0.02
    detector_error_rate_multiplier: float = 3.0
    detector_latency_multiplier: float = 2.0
    detector_latency_floor_ms: float = 500.0
    detector_cooldown_s: int = 300

    # --- Analysis (P9) ------------------------------------------------------
    analysis_pre_s: int = 300
    analysis_max_traces: int = 25
    analysis_max_logs_per_service: int = 100
    suspect_weight_error_origin: float = 0.30
    suspect_weight_onset: float = 0.20
    suspect_weight_local_ratio: float = 0.20
    suspect_weight_latency: float = 0.10
    suspect_weight_throughput: float = 0.10
    suspect_weight_container: float = 0.10

    # --- Faults -------------------------------------------------------------
    fault_default_ttl_s: int = 900
    fault_max_ttl_s: int = 3600
    fault_audit_file: Path = ROOT_DIR / "runtime" / "audit" / "faults.jsonl"
    fault_probe_window: str = "2m"
    fault_observer_interval_s: float = 5.0
    fault_watchdog_interval_s: float = 2.0
    fault_watchdog_enabled: bool = True
    fault_revert_timeout_s: float = 30.0
    flagd_flags_file: Path = ROOT_DIR / "runtime" / "flagd" / "demo.flagd.json"

    @property
    def jaeger_api_url(self) -> str:
        return f"{self.jaeger_url.rstrip('/')}{self.jaeger_base_path.rstrip('/')}"

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key.strip())


settings = Settings()
