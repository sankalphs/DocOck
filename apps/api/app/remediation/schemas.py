"""Remediation API models. No model carries a token or a token hash."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    # The incident left AWAITING_APPROVAL without a decision (the symptom
    # cleared, or it was closed by hand); nothing may execute any more.
    WITHDRAWN = "WITHDRAWN"


class ActionStatus(StrEnum):
    PLANNED = "PLANNED"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    WITHDRAWN = "WITHDRAWN"


class PreconditionView(BaseModel):
    name: str
    ok: bool
    detail: str


class PlanCheck(BaseModel):
    """What an action would do now, and whether it may."""

    action_id: str
    target: str
    ok: bool
    risk: str
    approval_mode: str
    description: str
    verification: str
    preconditions: list[PreconditionView] = Field(default_factory=list)
    faults: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    rollback: dict[str, Any] = Field(default_factory=dict)


class ActionView(BaseModel):
    id: int
    incident_ref: str
    action_id: str
    target: str
    status: ActionStatus
    risk: str | None
    approval_mode: str | None
    description: str
    verification: str
    requested_by: str | None
    diagnosis_id: int | None
    args: dict[str, Any]
    pre_snapshot: dict[str, Any] | None
    post_snapshot: dict[str, Any] | None
    result: dict[str, Any] | None
    rollback: dict[str, Any] | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ApprovalView(BaseModel):
    id: int
    status: ApprovalStatus
    incident_ref: str
    incident_status: str
    action: ActionView
    expires_at: datetime
    ttl_remaining_s: int
    decided_at: datetime | None
    decided_by: str | None
    note: str | None
    token_issued: bool
    token_used: bool
    created_at: datetime


class ApprovalList(BaseModel):
    items: list[ApprovalView]
    total: int
    pending: int
    generated_at: datetime


class PlanRequest(BaseModel):
    """Propose an action. Empty body = the diagnosis' recommended action."""

    model_config = ConfigDict(extra="forbid")

    action_id: str | None = Field(default=None, max_length=48)
    target: str | None = Field(default=None, max_length=64)
    ttl_s: int | None = Field(default=None, ge=15, le=3600)
    # Supersede a PENDING proposal (it is withdrawn); otherwise 409.
    replace: bool = False
    actor: str = Field(default="operator", min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=500)


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(default="operator", min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=500)


class PlanResult(BaseModel):
    approval: ApprovalView | None = None
    escalated: bool = False
    auto_approved: bool = False
    detail: str


class DryRunResult(BaseModel):
    approval_id: int
    would_execute: bool
    check: PlanCheck
    generated_at: datetime


__all__ = [
    "ActionStatus",
    "ActionView",
    "ApprovalList",
    "ApprovalStatus",
    "ApprovalView",
    "DecisionRequest",
    "DryRunResult",
    "PlanCheck",
    "PlanRequest",
    "PlanResult",
    "PreconditionView",
]
