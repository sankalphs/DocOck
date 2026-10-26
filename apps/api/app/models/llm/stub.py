"""Offline, deterministic provider.

It never touches the network, so the test suite and a key-less lab both work.
Three modes, in priority order:

1. a **script** (tests): a queue of responses returned one per call;
2. a **policy** callable: a deterministic reasoner registered by the caller (the
   agent registers one that follows the same tool protocol as a real model);
3. otherwise it answers `ok` (or `{"ok": true}` in JSON mode) — enough for the
   connection test.

Usage is estimated at 4 characters per token so budgets and accounting run the
same code path as a real provider; cost is always 0.
"""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable, Iterable

from app.models.llm.base import (
    ChatMessage,
    ChatResponse,
    LLMProvider,
    ToolCall,
    ToolSpec,
    Usage,
)

STUB_MODEL = "stub-deterministic-v1"

StubReply = ChatResponse | ToolCall | list[ToolCall] | str
StubPolicy = Callable[[list[ChatMessage], list[ToolSpec] | None, bool], StubReply]


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


class StubLLMProvider(LLMProvider):
    name = "stub"

    def __init__(
        self,
        *,
        model: str = STUB_MODEL,
        script: Iterable[StubReply] | None = None,
        policy: StubPolicy | None = None,
    ) -> None:
        self.model = model or STUB_MODEL
        self._script: deque[StubReply] = deque(script or [])
        self.policy = policy
        self.calls: list[list[ChatMessage]] = []

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
        started = time.perf_counter()
        self.calls.append(list(messages))
        if self._script:
            reply: StubReply = self._script.popleft()
        elif self.policy is not None:
            reply = self.policy(messages, tools, json_mode)
        else:
            reply = json.dumps({"ok": True}) if json_mode else "ok"
        response = self._wrap(reply)
        prompt = sum(
            estimate_tokens(m.content or "") + sum(len(c.raw_arguments) // 4 for c in m.tool_calls)
            for m in messages
        )
        completion = estimate_tokens(response.content or "") + sum(
            estimate_tokens(call.raw_arguments) for call in response.tool_calls
        )
        return response.model_copy(
            update={
                "usage": Usage(tokens_in=prompt, tokens_out=completion),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        )

    def _wrap(self, reply: StubReply) -> ChatResponse:
        if isinstance(reply, ChatResponse):
            return reply.model_copy(update={"provider": self.name, "model": self.model})
        if isinstance(reply, ToolCall):
            reply = [reply]
        if isinstance(reply, list):
            return ChatResponse(
                provider=self.name, model=self.model, tool_calls=reply, finish_reason="tool_calls"
            )
        return ChatResponse(
            provider=self.name, model=self.model, content=reply, finish_reason="stop"
        )


__all__ = ["STUB_MODEL", "StubLLMProvider", "StubPolicy", "StubReply", "estimate_tokens"]
