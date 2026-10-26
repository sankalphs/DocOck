"""Verification results and incident reports (P15)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.db.models import Action, Verification
from app.db.session import SessionFactory
from app.incidents.service import service as incident_service
from app.reports.service import ReportList, reports
from app.verification.verifier import verifier

router = APIRouter(tags=["reports"])


class VerificationView(BaseModel):
    id: int
    action_row_id: int
    action_id: str
    target: str
    verdict: str
    observed_for_s: int
    criteria: dict[str, Any]
    before: dict[str, Any]
    after: dict[str, Any]
    created_at: datetime


class VerificationList(BaseModel):
    items: list[VerificationView]
    running: list[int]


@router.get(
    "/incidents/{ref}/verifications",
    response_model=VerificationList,
    summary="Verification verdicts of one incident, with the actions still being verified",
)
async def incident_verifications(ref: str) -> VerificationList:
    async with SessionFactory() as session:
        incident = await incident_service.get(session, ref)
        rows = (
            await session.execute(
                select(Verification, Action)
                .join(Action, Action.id == Verification.action_row_id)
                .where(Action.incident_id == incident.id)
                .order_by(Verification.id)
            )
        ).all()
        action_ids = set(
            (await session.execute(select(Action.id).where(Action.incident_id == incident.id)))
            .scalars()
            .all()
        )
    return VerificationList(
        items=[
            VerificationView(
                id=row.id,
                action_row_id=row.action_row_id,
                action_id=action.action_id,
                target=action.target,
                verdict=row.verdict,
                observed_for_s=row.observed_for_s,
                criteria=dict(row.criteria or {}),
                before=dict(row.before or {}),
                after=dict(row.after or {}),
                created_at=row.created_at,
            )
            for row, action in rows
        ],
        running=[row for row in verifier.running() if row in action_ids],
    )


@router.get("/reports", response_model=ReportList, summary="Incidents with their report status")
async def list_reports(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> ReportList:
    return await reports.list(limit=limit)


@router.get(
    "/incidents/{ref}/report",
    response_model=dict[str, Any],
    summary="Self-contained incident report (JSON)",
)
async def incident_report(ref: str, refresh: bool = False) -> dict[str, Any]:
    return await reports.document(ref, refresh=refresh)


@router.get(
    "/incidents/{ref}/report.md",
    response_class=PlainTextResponse,
    summary="Incident report as Markdown (download=true for an attachment)",
)
async def incident_report_markdown(
    ref: str, refresh: bool = False, download: bool = False
) -> PlainTextResponse:
    text = await reports.markdown(ref, refresh=refresh)
    headers = {"Content-Disposition": f'attachment; filename="{ref}.md"'} if download else {}
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8", headers=headers)
