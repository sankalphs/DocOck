"""The investigation loop (PLAN §11).

    context → [memory hypotheses, P12] → LLM tool loop (≤ 20 calls, wall clock)
            → diagnosis JSON → validator (one repair) → confidence gate
            → incident DIAGNOSED | ESCALATED

Every step is persisted (`agent_steps`) and published on SSE as `agent.step`, so
the UI can replay a run or watch it live. A run never mutates infrastructure:
tools are read-only and remediation is chosen from the catalog later, in code.

Incident state is changed only by a run that took the incident from OPEN to
INVESTIGATING. A re-run on a DIAGNOSED/ESCALATED incident records a new run and
diagnosis without moving the incident.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.budget import Budget, BudgetExceeded
from app.agent.context import AgentContext, build_context
from app.agent.prompts import (
    FINALISE_MESSAGE,
    SYSTEM_PROMPT,
    initial_user_message,
    repair_message,
)
from app.agent.schemas import (
    AgentRunDetail,
    AgentRunSummary,
    AgentStepView,
    DiagnosisView,
    RunStatus,
    ValidationReport,
)
from app.agent.stub_policy import investigator_policy
from app.agent.tools import (
    MAX_OUTPUT_CHARS,
    ToolContext,
    ToolError,
    call_tool,
    incident_window,
    tool_specs,
)
from app.agent.validator import ValidationContext, extract_json, validate
from app.analysis.engine import Analyzer
from app.analysis.engine import analyzer as default_analyzer
from app.config import settings
from app.db.models import AgentRun, AgentStep, Diagnosis, Incident, Suspect
from app.db.session import SessionFactory
from app.domain.state import IncidentStatus
from app.errors import ApiError
from app.events.bus import bus
from app.incidents.service import service as incident_service
from app.logging import get_logger
from app.models.gateway import ModelGateway
from app.models.gateway import gateway as default_gateway
from app.models.llm.base import ChatMessage, ChatResponse, LLMError
from app.telemetry.docker import DockerAdapter
from app.telemetry.jaeger import JaegerAdapter
from app.telemetry.logs import LogsAdapter
from app.telemetry.prometheus import PrometheusAdapter

log = get_logger(__name__)

TOOL_TIMEOUT_S = 20.0
STEP_TEXT_LIMIT = 4000
# P12 sets this: (session, incident) -> hypotheses for the context.
HypothesisProvider = Callable[[AsyncSession, Incident], Awaitable[list[dict[str, Any]]]]


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _clip(value: Any) -> Any:
    """Keep step rows bounded: long strings are cut, structure is kept."""
    if isinstance(value, str):
        return value if len(value) <= STEP_TEXT_LIMIT else value[: STEP_TEXT_LIMIT - 1] + "…"
    if isinstance(value, dict):
        return {key: _clip(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clip(item) for item in value[:60]]
    return value


def diagnosis_view(row: Diagnosis) -> DiagnosisView:
    return DiagnosisView(
        id=row.id,
        root_cause_service=row.root_cause_service,
        fault_class=row.fault_class,
        confidence=row.confidence,
        summary=row.summary,
        evidence_codes=list(row.evidence_codes or []),
        affected_services=list(row.affected_services or []),
        recommended_action=dict(row.recommended_action or {}),
        alternative_hypotheses=list(row.alternative_hypotheses or []),
        valid=row.valid,
        validation_errors=list(row.validation_errors or []),
        created_at=_utc(row.created_at),
    )


class AgentOrchestrator:
    def __init__(
        self,
        *,
        gateway: ModelGateway | None = None,
        analyzer: Analyzer | None = None,
        prometheus: PrometheusAdapter | None = None,
        jaeger: JaegerAdapter | None = None,
        logs: LogsAdapter | None = None,
        docker: DockerAdapter | None = None,
    ) -> None:
        self.gateway = gateway or default_gateway
        self.analyzer = analyzer or default_analyzer
        self.prometheus = prometheus or PrometheusAdapter(ttl_s=0)
        self.jaeger = jaeger or JaegerAdapter(ttl_s=0)
        self.logs = logs or LogsAdapter(ttl_s=0)
        self.docker = docker or DockerAdapter(ttl_s=0)
        self.hypotheses: HypothesisProvider | None = None
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self.gateway.set_stub_policy(investigator_policy)

    # --- lifecycle --------------------------------------------------------------
    async def recover_stale(self) -> int:
        """A run left RUNNING by a crashed API can never finish; fail it."""
        async with SessionFactory() as session:
            stale = (
                (await session.execute(select(AgentRun).where(AgentRun.status == "RUNNING")))
                .scalars()
                .all()
            )
            for run in stale:
                run.status = RunStatus.FAILED.value
                run.error = "interrupted: the API restarted during the run"
                run.finished_at = datetime.now(UTC)
                incident = await session.get(Incident, run.incident_id)
                if incident is not None and incident.status == IncidentStatus.INVESTIGATING:
                    await incident_service.transition(
                        session,
                        incident,
                        IncidentStatus.ESCALATED,
                        kind="agent",
                        payload={"run_id": run.id, "reason": "run interrupted by API restart"},
                        actor="agent",
                    )
            await session.commit()
        return len(stale)

    async def spent_today(self, session: AsyncSession) -> float:
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        total = await session.scalar(
            select(func.coalesce(func.sum(AgentRun.cost_usd), 0)).where(
                AgentRun.started_at >= midnight
            )
        )
        return float(total or 0)

    async def start(self, ref: str, *, actor: str = "operator") -> AgentRunSummary:
        cfg = self.gateway.config
        if not self.gateway.llm_ready:
            raise ApiError(
                409,
                "llm_unconfigured",
                f"{cfg.llm.provider} has no API key; set LLM_API_KEY or switch to the stub",
            )
        async with SessionFactory() as session:
            incident = await incident_service.get(session, ref)
            if incident.status == IncidentStatus.RESOLVED:
                raise ApiError(409, "incident_closed", f"{incident.public_id} is resolved")
            running = await session.scalar(
                select(AgentRun).where(
                    AgentRun.incident_id == incident.id, AgentRun.status == RunStatus.RUNNING
                )
            )
            if running is not None:
                raise ApiError(
                    409,
                    "run_in_progress",
                    f"run {running.id} is still investigating {incident.public_id}",
                    details={"run_id": running.id},
                )
            spent = await self.spent_today(session)
            if cfg.llm.daily_budget_usd > 0 and spent >= cfg.llm.daily_budget_usd:
                raise ApiError(
                    429,
                    "llm_budget_exhausted",
                    f"daily LLM budget spent: ${spent:.4f} of ${cfg.llm.daily_budget_usd:.2f}",
                )
            provider = self.gateway.llm()
            run = AgentRun(
                incident_id=incident.id,
                status=RunStatus.RUNNING.value,
                provider=provider.name,
                model=provider.model,
            )
            session.add(run)
            await session.flush()
            owns_incident = incident.status == IncidentStatus.OPEN
            if owns_incident:
                await incident_service.transition(
                    session,
                    incident,
                    IncidentStatus.INVESTIGATING,
                    kind="agent",
                    payload={"run_id": run.id, "provider": provider.name, "model": provider.model},
                    actor=actor,
                )
            elif incident.status == IncidentStatus.INVESTIGATING:
                # A previous run died without finishing; this run owns it now.
                owns_incident = True
            await session.commit()
            summary = await self._summary(session, run, incident.public_id)

        bus.publish(
            "agent.run.started",
            {
                "run_id": summary.id,
                "incident_id": summary.incident_ref,
                "provider": summary.provider,
                "model": summary.model,
                "actor": actor,
            },
        )
        task = asyncio.create_task(
            self._run(summary.id, incident.id, owns_incident=owns_incident, spent_today=spent),
            name=f"agent-run-{summary.id}",
        )
        self._tasks[summary.id] = task
        run_id = summary.id
        task.add_done_callback(lambda _t: self._tasks.pop(run_id, None))
        return summary

    async def wait(self, run_id: int, timeout_s: float) -> None:
        task = self._tasks.get(run_id)
        if task is not None:
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)

    async def cancel(self, run_id: int) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        return True

    async def shutdown(self) -> None:
        for run_id in list(self._tasks):
            await self.cancel(run_id)

    # --- the run ------------------------------------------------------------------
    async def _run(
        self, run_id: int, incident_id: int, *, owns_incident: bool, spent_today: float
    ) -> None:
        cfg = self.gateway.config
        budget = Budget(
            max_tool_calls=cfg.llm.max_tool_calls,
            wall_clock_s=float(cfg.agent.wall_clock_s),
            spent_today_usd=spent_today,
            daily_budget_usd=cfg.llm.daily_budget_usd,
        )
        recorder = _StepRecorder(run_id)
        outcome: dict[str, Any] = {"status": RunStatus.FAILED, "error": None}
        incident_ref = ""
        try:
            # The hard cap covers a hung tool or analysis; the soft reserve in the
            # budget is what normally ends exploration.
            async with asyncio.timeout(budget.wall_clock_s + 30):
                outcome = await self._investigate(run_id, incident_id, budget, recorder)
        except asyncio.CancelledError:
            outcome = {"status": RunStatus.CANCELLED, "error": "cancelled by operator"}
            await recorder.add("cancelled", output={"reason": "cancelled by operator"})
        except TimeoutError:
            outcome = {"status": RunStatus.FAILED, "error": "hard wall clock exceeded"}
            await recorder.add("error", output={"error": outcome["error"]})
        except BudgetExceeded as exc:
            outcome = {"status": RunStatus.FAILED, "error": f"budget: {exc.message}"}
            await recorder.add("budget", output={"kind": exc.kind, "error": exc.message})
        except LLMError as exc:
            outcome = {"status": RunStatus.FAILED, "error": f"llm {exc.kind}: {exc.message}"}
            await recorder.add("error", output={"kind": exc.kind, "error": exc.message})
        except Exception as exc:  # surfaced on the run, never swallowed silently
            log.exception("agent_run_crashed", run_id=run_id)
            outcome = {"status": RunStatus.FAILED, "error": f"{type(exc).__name__}: {exc}"}
            await recorder.add("error", output={"error": outcome["error"]})
        finally:
            incident_ref = await self._finish(
                run_id, incident_id, budget, outcome, owns_incident=owns_incident
            )
        bus.publish(
            "agent.run.finished",
            {
                "run_id": run_id,
                "incident_id": incident_ref,
                "status": str(outcome["status"]),
                "error": outcome.get("error"),
                "tool_calls": budget.tool_calls,
                "llm_calls": budget.llm_calls,
                "cost_usd": round(budget.cost_usd, 6),
                "elapsed_s": round(budget.elapsed_s, 1),
            },
        )

    async def _investigate(
        self, run_id: int, incident_id: int, budget: Budget, recorder: _StepRecorder
    ) -> dict[str, Any]:
        cfg = self.gateway.config
        budget.check_start()
        async with SessionFactory() as session:
            incident = await session.get(Incident, incident_id)
            assert incident is not None
            recorder.incident_ref = incident.public_id
            has_suspects = await session.scalar(
                select(func.count()).select_from(Suspect).where(Suspect.incident_id == incident_id)
            )
        if not has_suspects:
            started = time.perf_counter()
            analysis = await self.analyzer.analyze(recorder.incident_ref)
            await recorder.add(
                "analysis",
                output={
                    "evidence_total": analysis.evidence_total,
                    "evidence_added": analysis.evidence_added,
                    "top_suspect": analysis.top_suspect,
                },
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        async with SessionFactory() as session:
            incident = await session.get(Incident, incident_id)
            assert incident is not None
            start, end = incident_window(incident, pre_s=settings.analysis_pre_s)
            hypotheses = await self.hypotheses(session, incident) if self.hypotheses else []
            context = await build_context(
                session,
                incident,
                window=(start.isoformat(), end.isoformat()),
                hypotheses=hypotheses,
            )
            opened = _utc(incident.opened_at)
        await recorder.add(
            "context",
            output={
                "codes_shown": len(context.shown_codes),
                "evidence_total": len(context.incident_codes),
                "suspects": [
                    {"service": item["service"], "score": item["score"]}
                    for item in context.payload["suspects"]
                ],
                "hypotheses": len(hypotheses),
                "services": sorted(context.topology_services),
                "chars": len(context.as_text()),
            },
        )

        tool_ctx = ToolContext(
            incident_id=incident_id,
            incident_ref=recorder.incident_ref,
            opened_at=opened,
            window_start=start,
            window_end=end,
            known_services=context.known_services,
            prometheus=self.prometheus,
            jaeger=self.jaeger,
            logs=self.logs,
            docker=self.docker,
        )
        llm = self.gateway.llm()
        specs = tool_specs()
        messages = [
            ChatMessage(role="system", content=SYSTEM_PROMPT),
            ChatMessage(
                role="user",
                content=initial_user_message(context.as_text(), tool_budget=budget.max_tool_calls),
            ),
        ]

        final_text: str | None = None
        force_final = False
        while final_text is None:
            finishing = force_final or budget.must_finish
            response = await self._chat(
                llm,
                messages,
                budget,
                recorder,
                tools=None if finishing else specs,
                finishing=finishing,
            )
            messages.append(response.as_message())
            if response.tool_calls:
                for call in response.tool_calls:
                    content = await self._tool(
                        call.name,
                        call.arguments,
                        call.raw_arguments,
                        tool_ctx,
                        context,
                        budget,
                        recorder,
                        finishing,
                    )
                    messages.append(
                        ChatMessage(
                            role="tool", tool_call_id=call.id, name=call.name, content=content
                        )
                    )
                if finishing or budget.must_finish:
                    force_final = True
                    messages.append(ChatMessage(role="user", content=FINALISE_MESSAGE))
                continue
            if response.content and response.content.strip():
                final_text = response.content
            else:
                force_final = True
                messages.append(ChatMessage(role="user", content=FINALISE_MESSAGE))

        report = self._validate(final_text, context)
        await recorder.add("validation", output=_report_out(report, attempt=1))
        if not report.valid:
            messages.append(ChatMessage(role="user", content=repair_message(report.errors)))
            response = await self._chat(
                llm, messages, budget, recorder, tools=None, finishing=True, kind="repair"
            )
            report = self._validate(response.content, context)
            await recorder.add("validation", output=_report_out(report, attempt=2))

        diagnosis_id = await self._store_diagnosis(run_id, report)
        if report.diagnosis is not None:
            await recorder.add(
                "diagnosis",
                output={
                    "diagnosis_id": diagnosis_id,
                    "valid": report.valid,
                    **report.diagnosis.model_dump(mode="json"),
                },
            )
        passes = report.passes_gate
        await recorder.add(
            "gate",
            output={
                "threshold": cfg.agent.confidence_threshold,
                "confidence": report.diagnosis.confidence if report.diagnosis else None,
                "valid": report.valid,
                "passed": passes,
            },
        )
        if passes:
            return {"status": RunStatus.DIAGNOSED, "error": None, "report": report}
        reason = (
            "invalid diagnosis after one repair: " + "; ".join(report.errors[:3])
            if not report.valid
            else f"confidence below {cfg.agent.confidence_threshold}"
        )
        return {"status": RunStatus.ESCALATED, "error": reason, "report": report}

    def _validate(self, text: str | None, context: AgentContext) -> ValidationReport:
        cfg = self.gateway.config
        return validate(
            extract_json(text),
            ValidationContext(
                incident_codes=context.incident_codes,
                shown_codes=context.shown_codes,
                evidence_data=context.evidence_data,
                topology_services=context.topology_services,
                known_services=context.known_services,
                fault_targets=context.fault_targets,
                flag_targets=context.flag_targets,
                threshold=cfg.agent.confidence_threshold,
            ),
        )

    async def _chat(
        self,
        llm: Any,
        messages: list[ChatMessage],
        budget: Budget,
        recorder: _StepRecorder,
        *,
        tools: Any,
        finishing: bool,
        kind: str = "llm",
    ) -> ChatResponse:
        cfg = self.gateway.config
        budget.before_llm()
        timeout = max(5.0, min(float(cfg.llm.timeout_s), budget.remaining_s))
        response: ChatResponse = await llm.chat(
            messages,
            tools=tools,
            json_mode=bool(finishing and cfg.llm.json_mode),
            timeout_s=timeout,
        )
        budget.charge_llm(response)
        await recorder.add(
            kind,
            input={
                "turn": budget.llm_calls,
                "tools_offered": bool(tools),
                "messages": len(messages),
            },
            output={
                "content": response.content,
                "tool_calls": [
                    {"name": call.name, "arguments": call.arguments or call.raw_arguments}
                    for call in response.tool_calls
                ],
                "finish_reason": response.finish_reason,
                "model": response.model,
                "tokens_in": response.usage.tokens_in,
                "tokens_out": response.usage.tokens_out,
                "cost_usd": response.cost_usd,
                "attempts": response.attempts,
            },
            latency_ms=int(response.latency_ms),
            usage=(response.usage.tokens_in, response.usage.tokens_out, response.cost_usd),
        )
        return response

    async def _tool(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        raw: str,
        tool_ctx: ToolContext,
        context: AgentContext,
        budget: Budget,
        recorder: _StepRecorder,
        finishing: bool,
    ) -> str:
        started = time.perf_counter()
        codes: list[str] = []
        if finishing or not budget.take_tool():
            output: dict[str, Any] = {
                "error": f"tool budget exhausted ({budget.max_tool_calls} calls); answer now"
            }
        else:
            try:
                result = await asyncio.wait_for(
                    call_tool(tool_ctx, name, arguments), timeout=TOOL_TIMEOUT_S
                )
                output, codes = result.output, result.codes
            except ToolError as exc:
                output = {"error": str(exc)}
            except TimeoutError:
                output = {"error": f"{name} timed out after {TOOL_TIMEOUT_S:.0f}s"}
            except ApiError as exc:
                output = {"error": f"{exc.code}: {exc.message}"}
            except Exception as exc:  # dependency down: tell the model, keep going
                output = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        if codes:
            context.shown_codes.update(codes)
            context.incident_codes.update(codes)
            await self._refresh_evidence(tool_ctx.incident_id, context, codes)
        text = json.dumps(output, separators=(",", ":"), default=str)
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[: MAX_OUTPUT_CHARS - 20] + '…(truncated)"}'
        await recorder.add(
            "tool",
            tool=name,
            input=arguments if arguments is not None else {"raw": raw},
            output={**output, "codes": codes} if codes else output,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return text

    async def _refresh_evidence(
        self, incident_id: int, context: AgentContext, codes: list[str]
    ) -> None:
        """Tool evidence joins the validator's view (existence + number tracing)."""
        from app.db.models import Evidence

        async with SessionFactory() as session:
            rows = (
                (
                    await session.execute(
                        select(Evidence).where(
                            Evidence.incident_id == incident_id, Evidence.code.in_(codes)
                        )
                    )
                )
                .scalars()
                .all()
            )
        for row in rows:
            context.evidence_data[row.code] = row.data
            if row.service:
                context.topology_services.add(row.service)
                context.known_services.add(row.service)

    async def _store_diagnosis(self, run_id: int, report: ValidationReport) -> int | None:
        if report.diagnosis is None:
            return None
        diagnosis = report.diagnosis
        async with SessionFactory() as session:
            row = Diagnosis(
                run_id=run_id,
                root_cause_service=diagnosis.root_cause.service,
                fault_class=diagnosis.root_cause.fault_class,
                confidence=diagnosis.confidence,
                summary=diagnosis.summary,
                evidence_codes=diagnosis.evidence_codes,
                affected_services=diagnosis.affected_services,
                recommended_action=diagnosis.recommended_action.model_dump(),
                alternative_hypotheses=[
                    item.model_dump() for item in diagnosis.alternative_hypotheses
                ],
                valid=report.valid,
                validation_errors=report.errors,
            )
            session.add(row)
            await session.commit()
            return row.id

    async def _finish(
        self,
        run_id: int,
        incident_id: int,
        budget: Budget,
        outcome: dict[str, Any],
        *,
        owns_incident: bool,
    ) -> str:
        status: RunStatus = outcome["status"]
        async with SessionFactory() as session:
            run = await session.get(AgentRun, run_id)
            incident = await session.get(Incident, incident_id)
            assert run is not None and incident is not None
            run.status = status.value
            run.error = outcome.get("error")
            run.finished_at = datetime.now(UTC)
            run.latency_ms = int(budget.elapsed_s * 1000)
            run.tokens_in = budget.tokens_in
            run.tokens_out = budget.tokens_out
            run.cost_usd = Decimal(str(round(budget.cost_usd, 6)))
            await session.flush()
            if owns_incident and incident.status == IncidentStatus.INVESTIGATING:
                report: ValidationReport | None = outcome.get("report")
                target = (
                    IncidentStatus.DIAGNOSED
                    if status == RunStatus.DIAGNOSED
                    else IncidentStatus.ESCALATED
                )
                payload: dict[str, Any] = {"run_id": run_id, "run_status": status.value}
                if report is not None and report.diagnosis is not None:
                    payload.update(
                        {
                            "root_cause": report.diagnosis.root_cause.service,
                            "fault_class": report.diagnosis.root_cause.fault_class,
                            "confidence": report.diagnosis.confidence,
                            "valid": report.valid,
                        }
                    )
                if outcome.get("error"):
                    payload["reason"] = outcome["error"]
                await incident_service.transition(
                    session, incident, target, kind="agent", payload=payload, actor="agent"
                )
            await session.commit()
            log.info(
                "agent_run_finished",
                run_id=run_id,
                incident=incident.public_id,
                status=status.value,
                tools=budget.tool_calls,
                llm=budget.llm_calls,
                cost=round(budget.cost_usd, 6),
                elapsed_s=round(budget.elapsed_s, 1),
            )
            return incident.public_id

    # --- reads --------------------------------------------------------------------
    async def _summary(
        self, session: AsyncSession, run: AgentRun, incident_ref: str
    ) -> AgentRunSummary:
        rows = (
            await session.execute(
                select(AgentStep.kind, func.count())
                .where(AgentStep.run_id == run.id)
                .group_by(AgentStep.kind)
            )
        ).all()
        counts = {str(kind): int(total) for kind, total in rows}
        diagnosis = await session.scalar(
            select(Diagnosis).where(Diagnosis.run_id == run.id).order_by(Diagnosis.id.desc())
        )
        return AgentRunSummary(
            id=run.id,
            incident_id=run.incident_id,
            incident_ref=incident_ref,
            status=run.status,
            provider=run.provider,
            model=run.model,
            tokens_in=run.tokens_in,
            tokens_out=run.tokens_out,
            cost_usd=float(run.cost_usd or 0),
            error=run.error,
            started_at=_utc(run.started_at),
            finished_at=_utc(run.finished_at) if run.finished_at else None,
            latency_ms=run.latency_ms,
            tool_calls=int(counts.get("tool", 0)),
            llm_calls=int(counts.get("llm", 0)) + int(counts.get("repair", 0)),
            diagnosis=diagnosis_view(diagnosis) if diagnosis else None,
        )

    async def get_run(self, run_id: int) -> AgentRunDetail:
        async with SessionFactory() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                raise ApiError(404, "run_not_found", f"agent run {run_id} not found")
            incident = await session.get(Incident, run.incident_id)
            summary = await self._summary(session, run, incident.public_id if incident else "")
            steps = (
                (
                    await session.execute(
                        select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.seq)
                    )
                )
                .scalars()
                .all()
            )
        return AgentRunDetail(
            **summary.model_dump(),
            steps=[
                AgentStepView(
                    seq=step.seq,
                    kind=step.kind,
                    tool=step.tool,
                    input=step.input,
                    output=step.output,
                    latency_ms=step.latency_ms,
                    at=_utc(step.at),
                )
                for step in steps
            ],
        )

    async def list_runs(
        self, *, incident_ref: str | None = None, limit: int = 20
    ) -> list[AgentRunSummary]:
        async with SessionFactory() as session:
            query = select(AgentRun, Incident.public_id).join(
                Incident, Incident.id == AgentRun.incident_id
            )
            if incident_ref is not None:
                incident = await incident_service.get(session, incident_ref)
                query = query.where(AgentRun.incident_id == incident.id)
            rows = (await session.execute(query.order_by(AgentRun.id.desc()).limit(limit))).all()
            return [await self._summary(session, run, ref) for run, ref in rows]

    async def validate_for(self, ref: str, raw: dict[str, Any]) -> ValidationReport:
        """Validate an arbitrary diagnosis against an incident (debug/DoD endpoint).

        Every code of the incident counts as shown here: this checks the rules
        that do not depend on a particular run.
        """
        async with SessionFactory() as session:
            incident = await incident_service.get(session, ref)
            start, end = incident_window(incident, pre_s=settings.analysis_pre_s)
            context = await build_context(
                session, incident, window=(start.isoformat(), end.isoformat())
            )
        context.shown_codes = set(context.incident_codes)
        return validate(
            raw,
            ValidationContext(
                incident_codes=context.incident_codes,
                shown_codes=context.shown_codes,
                evidence_data=context.evidence_data,
                topology_services=context.topology_services,
                known_services=context.known_services,
                fault_targets=context.fault_targets,
                flag_targets=context.flag_targets,
                threshold=self.gateway.config.agent.confidence_threshold,
            ),
        )


def _report_out(report: ValidationReport, *, attempt: int) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "valid": report.valid,
        "passes_gate": report.passes_gate,
        "errors": report.errors,
        "warnings": report.warnings,
        "cited": report.diagnosis.evidence_codes if report.diagnosis else [],
    }


class _StepRecorder:
    """Persists steps in order and publishes each one on SSE."""

    def __init__(self, run_id: int) -> None:
        self.run_id = run_id
        self.seq = 0
        self.incident_ref = ""

    async def add(
        self,
        kind: str,
        *,
        tool: str | None = None,
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        latency_ms: int | None = None,
        usage: tuple[int, int, float] | None = None,
    ) -> None:
        self.seq += 1
        step = AgentStep(
            run_id=self.run_id,
            seq=self.seq,
            kind=kind,
            tool=tool,
            input=_clip(input) if input is not None else None,
            output=_clip(output) if output is not None else None,
            latency_ms=latency_ms,
        )
        async with SessionFactory() as session:
            session.add(step)
            if usage is not None:
                tokens_in, tokens_out, cost = usage
                await session.execute(
                    update(AgentRun)
                    .where(AgentRun.id == self.run_id)
                    .values(
                        tokens_in=AgentRun.tokens_in + tokens_in,
                        tokens_out=AgentRun.tokens_out + tokens_out,
                        cost_usd=AgentRun.cost_usd + Decimal(str(round(cost, 6))),
                    )
                )
            await session.commit()
        bus.publish(
            "agent.step",
            {
                "run_id": self.run_id,
                "incident_id": self.incident_ref,
                "seq": self.seq,
                "kind": kind,
                "tool": tool,
                "latency_ms": latency_ms,
                "summary": _step_summary(kind, tool, output),
            },
        )


def _step_summary(kind: str, tool: str | None, output: dict[str, Any] | None) -> str:
    output = output or {}
    if kind == "tool":
        if "error" in output:
            return f"{tool}: error {str(output['error'])[:120]}"
        code = output.get("evidence_code")
        return f"{tool} → {code}" if code else f"{tool} ok"
    if kind in ("llm", "repair"):
        calls = output.get("tool_calls") or []
        if calls:
            return "calls " + ", ".join(str(call.get("name")) for call in calls)
        return "answered"
    if kind == "validation":
        return "valid" if output.get("valid") else f"{len(output.get('errors') or [])} error(s)"
    if kind == "gate":
        return f"{'passed' if output.get('passed') else 'escalate'} at {output.get('confidence')}"
    if kind == "diagnosis":
        root = (output.get("root_cause") or {}).get("service")
        return f"root cause {root} ({output.get('confidence')})"
    if kind == "context":
        return f"{output.get('codes_shown')} codes shown"
    if kind == "analysis":
        return f"analysis: {output.get('evidence_total')} evidence, top {output.get('top_suspect')}"
    return str(output.get("error") or output.get("reason") or kind)[:160]


orchestrator = AgentOrchestrator()

__all__ = ["AgentOrchestrator", "diagnosis_view", "orchestrator"]
