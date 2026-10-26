"""Per-run limits (PLAN §11: 20 tool calls, 90 s wall clock) and the daily spend cap.

Limits are checked *before* the work they guard: a tool call that would be the
21st is refused, not executed and then counted. When the tool budget or the
wall-clock reserve runs out, the orchestrator asks for a final answer without
tools instead of failing outright; only the hard wall clock and the daily spend
cap end a run as FAILED.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.models.llm.base import ChatResponse

# Seconds kept back for the final (tool-less) answer and validation/repair.
FINAL_RESERVE_S = 25.0


class BudgetExceeded(Exception):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass
class Budget:
    max_tool_calls: int = 20
    wall_clock_s: float = 90.0
    # LLM turns: every tool round costs one; +4 covers the final answer and a repair.
    max_llm_calls: int = 0
    spent_today_usd: float = 0.0
    daily_budget_usd: float = 2.0
    started: float = field(default_factory=time.monotonic)
    tool_calls: int = 0
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    def __post_init__(self) -> None:
        if self.max_llm_calls <= 0:
            self.max_llm_calls = self.max_tool_calls + 4

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started

    @property
    def remaining_s(self) -> float:
        return self.wall_clock_s - self.elapsed_s

    @property
    def tools_left(self) -> int:
        return max(0, self.max_tool_calls - self.tool_calls)

    @property
    def must_finish(self) -> bool:
        """Time to stop exploring and ask for the answer."""
        return self.tools_left == 0 or self.remaining_s <= FINAL_RESERVE_S

    def check_start(self) -> None:
        if self.daily_budget_usd > 0 and self.spent_today_usd >= self.daily_budget_usd:
            raise BudgetExceeded(
                "daily_budget",
                f"daily LLM budget spent: ${self.spent_today_usd:.4f} of "
                f"${self.daily_budget_usd:.2f}",
            )

    def before_llm(self) -> None:
        if self.remaining_s <= 0:
            raise BudgetExceeded("wall_clock", f"wall clock {self.wall_clock_s:.0f}s exceeded")
        if self.llm_calls >= self.max_llm_calls:
            raise BudgetExceeded("llm_calls", f"{self.max_llm_calls} model calls exceeded")
        if (
            self.daily_budget_usd > 0
            and self.spent_today_usd + self.cost_usd >= self.daily_budget_usd
        ):
            raise BudgetExceeded("daily_budget", "daily LLM budget spent during the run")
        self.llm_calls += 1

    def charge_llm(self, response: ChatResponse) -> None:
        self.tokens_in += response.usage.tokens_in
        self.tokens_out += response.usage.tokens_out
        self.cost_usd += response.cost_usd

    def take_tool(self) -> bool:
        """Reserve one tool call; False when the budget is spent."""
        if self.tool_calls >= self.max_tool_calls:
            return False
        self.tool_calls += 1
        return True


__all__ = ["FINAL_RESERVE_S", "Budget", "BudgetExceeded"]
