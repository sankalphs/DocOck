"""The diagnosis contract (PLAN §11) and the run/step views.

The diagnosis schema has no field for memory citations: a retrieved past
incident can steer what the agent checks, never what it cites (P12).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FaultClass = Literal[
    "application_error",
    "resource_exhaustion",
    "dependency_unavailable",
    "network",
    "traffic",
    "configuration",
    "unknown",
]
FAULT_CLASSES: tuple[str, ...] = (
    "application_error",
    "resource_exhaustion",
    "dependency_unavailable",
    "network",
    "traffic",
    "configuration",
    "unknown",
)
SUMMARY_MAX_CHARS = 1200


class RootCause(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str = Field(min_length=1, max_length=64)
    fault_class: FaultClass


class RecommendedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str = Field(min_length=1, max_length=48)
    target: str = Field(min_length=1, max_length=64)


class AlternativeHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str = Field(min_length=1, max_length=64)
    confidence: float = Field(ge=0, le=1)


class Diagnosis(BaseModel):
    """Exactly the PLAN §11 shape. `extra=forbid` rejects smuggled fields."""

    model_config = ConfigDict(extra="forbid")
    root_cause: RootCause
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(min_length=1)
    evidence_codes: list[str] = Field(min_length=1, max_length=40)
    affected_services: list[str] = Field(default_factory=list, max_length=30)
    recommended_action: RecommendedAction
    alternative_hypotheses: list[AlternativeHypothesis] = Field(default_factory=list, max_length=5)


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    # A valid diagnosis at or above the confidence gate.
    DIAGNOSED = "DIAGNOSED"
    # Finished, but invalid after the repair attempt or below the gate.
    ESCALATED = "ESCALATED"
    # Provider error, budget, or wall clock; the error is on the run.
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.DIAGNOSED, RunStatus.ESCALATED, RunStatus.FAILED, RunStatus.CANCELLED}
)

StepKind = Literal[
    "context",
    "analysis",
    "llm",
    "tool",
    "validation",
    "repair",
    "diagnosis",
    "gate",
    "budget",
    "error",
    "cancelled",
]


class AgentStepView(BaseModel):
    seq: int
    kind: str
    tool: str | None = None
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    latency_ms: int | None = None
    at: datetime


class DiagnosisView(BaseModel):
    id: int
    root_cause_service: str
    fault_class: str
    confidence: float
    summary: str
    evidence_codes: list[str]
    affected_services: list[str]
    recommended_action: dict[str, Any]
    alternative_hypotheses: list[dict[str, Any]]
    valid: bool
    validation_errors: list[str]
    created_at: datetime


class AgentRunSummary(BaseModel):
    id: int
    incident_id: int
    incident_ref: str
    status: str
    provider: str | None
    model: str | None
    tokens_in: int
    tokens_out: int
    cost_usd: float
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    latency_ms: int | None
    tool_calls: int = 0
    llm_calls: int = 0
    diagnosis: DiagnosisView | None = None


class AgentRunDetail(AgentRunSummary):
    steps: list[AgentStepView] = Field(default_factory=list)


class AgentRunList(BaseModel):
    items: list[AgentRunSummary]
    total: int


class ValidationReport(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    passes_gate: bool = False
    threshold: float
    diagnosis: Diagnosis | None = None


__all__ = [
    "FAULT_CLASSES",
    "SUMMARY_MAX_CHARS",
    "TERMINAL_RUN_STATUSES",
    "AgentRunDetail",
    "AgentRunList",
    "AgentRunSummary",
    "AgentStepView",
    "AlternativeHypothesis",
    "Diagnosis",
    "DiagnosisView",
    "FaultClass",
    "RecommendedAction",
    "RootCause",
    "RunStatus",
    "StepKind",
    "ValidationReport",
]
