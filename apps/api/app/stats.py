"""Operating statistics (P16): agent cost and latency, outcomes, guardrails.

One read-only roll-up over a time window for the dashboard and the scenario
scoreboard: what the agent cost and how long it took, how incidents ended, what
verification decided, and whether the LLM budget and rate limits are biting.
Percentiles are computed in Python: the lab keeps hundreds of rows, not millions.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Action, AgentRun, Approval, Incident, Verification
from app.ratelimit import limiter


class Distribution(BaseModel):
    count: int
    p50: float | None
    p95: float | None
    max: float | None
    mean: float | None


class ModelUsage(BaseModel):
    provider: str
    model: str
    runs: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: Distribution


class AgentStats(BaseModel):
    runs: int
    by_status: dict[str, int]
    latency_ms: Distribution
    tokens_in: int
    tokens_out: int
    cost_usd: float
    spent_today_usd: float
    daily_budget_usd: float
    budget_used: float | None
    models: list[ModelUsage]


class IncidentStats(BaseModel):
    opened: int
    by_status: dict[str, int]
    open: int
    time_to_close_s: Distribution


class RemediationStats(BaseModel):
    actions: int
    by_status: dict[str, int]
    approvals: dict[str, int]
    verdicts: dict[str, int]
    verification_s: Distribution


class RateLimitRule(BaseModel):
    name: str
    pattern: str
    limit: int
    window_s: float


class RateLimitStats(BaseModel):
    enabled: bool
    rejected: int
    rules: list[RateLimitRule]


class Stats(BaseModel):
    generated_at: datetime
    since: datetime
    window_h: int
    agent: AgentStats
    incidents: IncidentStats
    remediation: RemediationStats
    rate_limits: RateLimitStats


TERMINAL = ("RESOLVED", "ESCALATED")


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile; None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(q * len(ordered))))
    return ordered[rank - 1]


def distribution(values: list[float]) -> Distribution:
    return Distribution(
        count=len(values),
        p50=percentile(values, 0.5),
        p95=percentile(values, 0.95),
        max=max(values) if values else None,
        mean=round(sum(values) / len(values), 1) if values else None,
    )


def _count(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _usd(value: Decimal | float | None) -> float:
    return round(float(value or 0), 6)


async def collect(session: AsyncSession, window_h: int = 168) -> Stats:
    now = datetime.now(UTC)
    since = now - timedelta(hours=window_h)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    runs = (
        (await session.execute(select(AgentRun).where(AgentRun.started_at >= since)))
        .scalars()
        .all()
    )
    finished = [run for run in runs if run.latency_ms is not None]
    today = (
        await session.execute(select(AgentRun.cost_usd).where(AgentRun.started_at >= midnight))
    ).scalars()
    spent_today = sum(_usd(cost) for cost in today)
    budget = settings.llm_daily_budget_usd
    groups: dict[tuple[str, str], list[AgentRun]] = {}
    for run in runs:
        groups.setdefault((run.provider or "unknown", run.model or "unknown"), []).append(run)
    agent = AgentStats(
        runs=len(runs),
        by_status=_count([run.status for run in runs]),
        latency_ms=distribution([float(run.latency_ms or 0) for run in finished]),
        tokens_in=sum(run.tokens_in for run in runs),
        tokens_out=sum(run.tokens_out for run in runs),
        cost_usd=round(sum(_usd(run.cost_usd) for run in runs), 6),
        spent_today_usd=round(spent_today, 6),
        daily_budget_usd=budget,
        budget_used=round(spent_today / budget, 4) if budget > 0 else None,
        models=[
            ModelUsage(
                provider=provider,
                model=model,
                runs=len(items),
                tokens_in=sum(run.tokens_in for run in items),
                tokens_out=sum(run.tokens_out for run in items),
                cost_usd=round(sum(_usd(run.cost_usd) for run in items), 6),
                latency_ms=distribution(
                    [float(run.latency_ms) for run in items if run.latency_ms is not None]
                ),
            )
            for (provider, model), items in sorted(groups.items())
        ],
    )

    incidents = (
        (await session.execute(select(Incident).where(Incident.opened_at >= since))).scalars().all()
    )
    open_now = (
        (await session.execute(select(Incident.id).where(Incident.status.not_in(TERMINAL))))
        .scalars()
        .all()
    )
    incident_stats = IncidentStats(
        opened=len(incidents),
        by_status=_count([incident.status for incident in incidents]),
        open=len(open_now),
        time_to_close_s=distribution(
            [
                (incident.closed_at - incident.opened_at).total_seconds()
                for incident in incidents
                if incident.closed_at is not None and incident.status in TERMINAL
            ]
        ),
    )

    actions = (
        (await session.execute(select(Action.status).where(Action.created_at >= since)))
        .scalars()
        .all()
    )
    approvals = (
        (await session.execute(select(Approval.status).where(Approval.created_at >= since)))
        .scalars()
        .all()
    )
    verifications = (
        await session.execute(
            select(Verification.verdict, Verification.observed_for_s).where(
                Verification.created_at >= since
            )
        )
    ).all()
    remediation = RemediationStats(
        actions=len(actions),
        by_status=_count(list(actions)),
        approvals=_count(list(approvals)),
        verdicts=_count([row.verdict for row in verifications]),
        verification_s=distribution([float(row.observed_for_s) for row in verifications]),
    )

    limits = limiter.status()
    return Stats(
        generated_at=now,
        since=since,
        window_h=window_h,
        agent=agent,
        incidents=incident_stats,
        remediation=remediation,
        rate_limits=RateLimitStats.model_validate(limits),
    )


__all__ = ["Stats", "collect", "distribution", "percentile"]
