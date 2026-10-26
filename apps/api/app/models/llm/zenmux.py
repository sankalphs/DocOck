"""ZenMux: an OpenAI-compatible router. Only the defaults and headers differ."""

from __future__ import annotations

from typing import Any

from app.models.llm.openai_compatible import OpenAICompatibleProvider

ZENMUX_BASE_URL = "https://zenmux.ai/api/v1"


class ZenMuxProvider(OpenAICompatibleProvider):
    name = "zenmux"

    def __init__(self, *, base_url: str = ZENMUX_BASE_URL, **kwargs: Any) -> None:
        headers = {"X-Title": "DocOck", **(kwargs.pop("extra_headers", None) or {})}
        super().__init__(base_url=base_url or ZENMUX_BASE_URL, extra_headers=headers, **kwargs)


__all__ = ["ZENMUX_BASE_URL", "ZenMuxProvider"]
