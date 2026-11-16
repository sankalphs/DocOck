"""LLM remediation planner: choose the next rung of the ladder and explain why.

The model sees the diagnosis, the drift, what was already tried (and how it
failed) and the candidate list built deterministically by `ladder.build`. It
answers with an index into that list and a short rationale. It cannot invent an
action or a target: anything outside the list, malformed JSON, a timeout or a
provider error falls back to the first candidate with a deterministic
rationale. With the offline `stub` provider the deterministic choice is used
directly, so the lab stays reproducible without a key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.agent.validator import extract_json
from app.autopilot.ladder import Candidate
from app.config import settings
from app.logging import get_logger
from app.models.gateway import ModelGateway
from app.models.gateway import gateway as default_gateway
from app.models.llm.base import ChatMessage, LLMError

log = get_logger(__name__)

SYSTEM = """You are DocOck's remediation planner. One incident is open in a
microservice lab. Pick the ONE next remediation to run from the numbered
candidate list, or -1 to hand the incident to a human.

Rules:
- Choose only an index from the list. You cannot add actions or targets.
- Prefer the action that removes the cause, not a symptom: a drifted
  dependency before its caller, a flag before a restart, the smallest blast
  radius first.
- Never re-pick something that already failed; read the attempt history.
- rationale: at most 400 characters, plain prose, no numbers you were not shown.

Reply with ONLY a JSON object: {"choice": <index>, "rationale": "<why>"}"""


@dataclass(slots=True)
class Choice:
    index: int  # -1 = escalate
    rationale: str
    by: str  # "llm" | "deterministic"
    cost_usd: float = 0.0
    tokens: int = 0
    error: str | None = None


def deterministic(candidates: list[Candidate], attempts: list[dict[str, Any]]) -> Choice:
    if not candidates:
        return Choice(-1, "no remediation candidate is left", "deterministic")
    first = candidates[0]
    tried = f" after {len(attempts)} failed attempt(s)" if attempts else ""
    return Choice(
        0,
        f"{first.action_id} on {first.target}{tried}: {first.reason} ({first.source}).",
        "deterministic",
    )


class RemediationPlanner:
    def __init__(self, gateway: ModelGateway | None = None) -> None:
        self.gateway = gateway or default_gateway

    @property
    def uses_llm(self) -> bool:
        return (
            settings.autopilot_llm_planner
            and self.gateway.config.llm.provider != "stub"
            and self.gateway.llm_ready
        )

    async def choose(
        self,
        candidates: list[Candidate],
        *,
        incident: dict[str, Any],
        diagnosis: dict[str, Any] | None,
        drift: list[dict[str, Any]],
        attempts: list[dict[str, Any]],
    ) -> Choice:
        fallback = deterministic(candidates, attempts)
        if not candidates or not self.uses_llm:
            return fallback
        payload = {
            "incident": incident,
            "diagnosis": diagnosis,
            "drift": drift[:12],
            "attempts": attempts[-6:],
            "candidates": [
                {"index": index, **candidate.as_dict()}
                for index, candidate in enumerate(candidates[:12])
            ],
        }
        messages = [
            ChatMessage(role="system", content=SYSTEM),
            ChatMessage(role="user", content=json.dumps(payload, default=str)),
        ]
        cfg = self.gateway.config.llm
        try:
            response = await self.gateway.llm().chat(
                messages,
                json_mode=cfg.json_mode,
                temperature=0.0,
                max_tokens=400,
                timeout_s=min(float(cfg.timeout_s), 45.0),
            )
        except LLMError as exc:
            fallback.error = f"llm {exc.kind}: {exc.message}"[:300]
            return fallback
        except Exception as exc:  # never let the planner block remediation
            fallback.error = f"{type(exc).__name__}: {exc}"[:300]
            return fallback
        raw = extract_json(response.content)
        tokens = response.usage.total
        index = raw.get("choice") if isinstance(raw, dict) else None
        rationale = str((raw or {}).get("rationale") or "").strip()[:400]
        if isinstance(index, bool) or not isinstance(index, int):
            fallback.error = "planner reply had no integer choice"
            fallback.cost_usd, fallback.tokens = response.cost_usd, tokens
            return fallback
        if index == -1:
            return Choice(
                -1, rationale or "the planner hands it to a human", "llm", response.cost_usd, tokens
            )
        if not 0 <= index < min(len(candidates), 12):
            fallback.error = f"planner chose {index}, outside the candidate list"
            fallback.cost_usd, fallback.tokens = response.cost_usd, tokens
            return fallback
        log.info("remediation_planned_by_llm", index=index, action=candidates[index].action_id)
        return Choice(
            index,
            rationale or candidates[index].reason,
            "llm",
            response.cost_usd,
            tokens,
        )


planner = RemediationPlanner()

__all__ = ["Choice", "RemediationPlanner", "deterministic", "planner"]
