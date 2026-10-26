"""Provider-neutral chat types and the `LLMProvider` contract.

The agent (P11) talks only to this interface. Every provider returns the same
`ChatResponse`: text and/or tool calls, token usage, cost, latency, and the model
ID the server *reported* (which can differ from the one requested when a router
such as ZenMux resolves an alias). A provider never logs or returns its API key.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    id: str
    name: str
    # The raw JSON text as the model produced it. `arguments` is the parsed
    # object, or None when the model emitted invalid JSON (the orchestrator
    # reports that back to the model instead of guessing).
    raw_arguments: str = "{}"
    arguments: dict[str, Any] | None = None


class ChatMessage(BaseModel):
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def to_wire(self) -> dict[str, Any]:
        """OpenAI chat-completions message shape."""
        message: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.raw_arguments},
                }
                for call in self.tool_calls
            ]
        if self.tool_call_id is not None:
            message["tool_call_id"] = self.tool_call_id
        if self.name is not None and self.role == "tool":
            message["name"] = self.name
        return message


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]

    def to_wire(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class Usage(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def total(self) -> int:
        return self.tokens_in + self.tokens_out


class ChatResponse(BaseModel):
    provider: str
    model: str
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    attempts: int = 1

    def as_message(self) -> ChatMessage:
        return ChatMessage(role="assistant", content=self.content, tool_calls=self.tool_calls)


class Pricing(BaseModel):
    """USD per million tokens. Cost is computed locally from reported usage."""

    input_per_m: float = 0.0
    output_per_m: float = 0.0

    def cost(self, usage: Usage) -> float:
        return round(
            usage.tokens_in * self.input_per_m / 1_000_000
            + usage.tokens_out * self.output_per_m / 1_000_000,
            8,
        )


LLMErrorKind = Literal[
    "unconfigured", "auth", "rate_limited", "timeout", "upstream", "bad_response", "budget"
]


class LLMError(Exception):
    """A provider failure with a stable kind the agent and the UI can branch on."""

    def __init__(self, kind: LLMErrorKind, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status_code = status_code

    @property
    def retryable(self) -> bool:
        return self.kind in {"rate_limited", "timeout", "upstream"}


def parse_arguments(raw: str | None) -> dict[str, Any] | None:
    if raw is None or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


class LLMProvider(ABC):
    """One reasoning model behind an OpenAI-compatible contract."""

    name: str
    model: str

    @property
    def configured(self) -> bool:
        return True

    @abstractmethod
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
        """One completion. Raises `LLMError` on failure, never returns partial data."""

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        """Release network resources."""


__all__ = [
    "ChatMessage",
    "ChatResponse",
    "LLMError",
    "LLMErrorKind",
    "LLMProvider",
    "Pricing",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "parse_arguments",
]
