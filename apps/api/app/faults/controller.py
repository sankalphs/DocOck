"""Fault lifecycle: validate → safety-check → inject → observe → revert.

The controller is the only writer. It owns:

- the per-mutation lock (a second operator cannot race an injection),
- the durable `faults` row (pre-image written *before* the side effect, so a
  crash mid-injection is still revertible),
- the audit trail and the SSE events,
- the startup reconciliation that makes an API kill harmless.

Status semantics:

- `PROPAGATING`: flagd is set and OFREP confirms it, but the service has not
  shown the symptom yet. The demo's flagd SDK clients take 30-45 s.
- `ACTIVE`: the mechanism is confirmed. For direct mechanisms that is the
  read-back of the container/load-generator change; for flagd it is the first
  observed symptom.
- `REVERTED` / `EXPIRED` / `FAILED`: terminal.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import settings
from app.db.models import Fault
from app.db.session import SessionFactory
from app.errors import ApiError
from app.events.bus import bus
from app.faults.audit import AuditEntry, AuditLog
from app.faults.catalog import (
    CLOSED_STATUSES,
    DEFINITIONS,
    OPEN_STATUSES,
    FaultCatalog,
    FaultCategory,
    FaultDefinition,
    FaultRequest,
    FaultStatus,
    Mechanism,
    NormalizedFault,
    SymptomSpec,
    catalog,
    resolve_request,
)
from app.faults.flagd import FlagdWriter, reset_drift
from app.faults.mechanisms import (
    FaultProbe,
    MechanismOutcome,
    Mechanisms,
    ProbeReading,
    host_cores,
    symptom_satisfied,
)
from app.faults.safety import ContainerTarget, resolve_target
from app.logging import get_logger
from app.telemetry.docker import DockerError, docker_inspect

log = get_logger(__name__)

ACTOR_MAX = 64
REVERT_ATTEMPT_LIMIT = 5


class FaultView(BaseModel):
    id: int
    fault_id: str
    label: str
    category: FaultCategory
    mechanism: Mechanism
    target: str
    params: dict[str, Any]
    status: FaultStatus
    actor: str
    risk: str
    injected_at: datetime
    expires_at: datetime | None
    reset_at: datetime | None
    ttl_remaining_s: int | None
    symptom: SymptomSpec
    observed: dict[str, Any] | None = None
    injection_ref: dict[str, Any] = Field(default_factory=dict)


class FaultList(BaseModel):
    items: list[FaultView]
    total: int
    open: int
    generated_at: datetime


class InjectionResult(BaseModel):
    dry_run: bool
    fault: FaultView | None = None
    plan: dict[str, Any] | None = None


class ResetReport(BaseModel):
    reverted: list[FaultView]
    failed: list[dict[str, Any]]
    flag_drift: list[dict[str, Any]]
    generated_at: datetime


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _view(row: Fault, *, now: datetime | None = None) -> FaultView:
    now = now or datetime.now(UTC)
    definition = DEFINITIONS.get(row.type)
    ref = dict(row.injection_ref or {})
    expires_at = _as_utc(row.expires_at) if row.expires_at else None
    remaining = max(0, int((expires_at - now).total_seconds())) if expires_at is not None else None
    return FaultView(
        id=row.id,
        fault_id=row.type,
        label=definition.label if definition else row.type,
        category=definition.category if definition else FaultCategory.INFRASTRUCTURE,
        mechanism=definition.mechanism if definition else Mechanism.FLAGD,
        target=row.target,
        params=dict(row.params or {}),
        status=FaultStatus(row.status),
        actor=row.actor,
        risk=definition.risk if definition else "medium",
        injected_at=_as_utc(row.injected_at),
        expires_at=expires_at,
        reset_at=_as_utc(row.reset_at) if row.reset_at else None,
        ttl_remaining_s=remaining,
        symptom=definition.symptom
        if definition
        else SymptomSpec(probe="error_rate", service=row.target),
        observed=ref.get("observed"),
        injection_ref=ref,
    )


class FaultController:
    def __init__(
        self,
        *,
        mechanisms: Mechanisms | None = None,
        probe: FaultProbe | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self.mechanisms = mechanisms or Mechanisms()
        self.probe = probe or FaultProbe()
        self.audit = audit or AuditLog()
        self._lock = asyncio.Lock()

    # --- reads -------------------------------------------------------------
    def catalog(self) -> FaultCatalog:
        return catalog()

    async def list_faults(self, *, status: str = "all", limit: int = 100) -> FaultList:
        query = select(Fault).order_by(Fault.id.desc()).limit(limit)
        if status == "open":
            query = query.where(Fault.status.in_([item.value for item in OPEN_STATUSES]))
        elif status == "closed":
            query = query.where(Fault.status.in_([item.value for item in CLOSED_STATUSES]))
        elif status not in ("all", ""):
            query = query.where(Fault.status == status)
        async with SessionFactory() as session:
            rows = list((await session.execute(query)).scalars().all())
        now = datetime.now(UTC)
        return FaultList(
            items=[_view(row, now=now) for row in rows],
            total=len(rows),
            open=sum(1 for row in rows if row.status in {s.value for s in OPEN_STATUSES}),
            generated_at=now,
        )

    async def get(self, row_id: int) -> FaultView:
        async with SessionFactory() as session:
            row = await session.get(Fault, row_id)
        if row is None:
            raise ApiError(404, "fault_not_found", f"Fault {row_id} does not exist")
        return _view(row)

    # --- preflight ---------------------------------------------------------
    async def _preflight(
        self, normalized: NormalizedFault, target: ContainerTarget
    ) -> dict[str, Any]:
        definition = normalized.definition
        mechanism = definition.mechanism
        pre: dict[str, Any] = {
            "mechanism": mechanism.value,
            "container": target.container,
            "service": normalized.target,
            "network": target.network or None,
            "aliases": target.network_aliases,
        }

        if mechanism == Mechanism.FLAGD:
            flag = definition.flag or ""
            variants = await asyncio.to_thread(self.mechanisms.flagd.current_variants)
            if flag not in variants:
                raise ApiError(
                    502,
                    "flagd_file_invalid",
                    f"flag '{flag}' is not in the runtime flag file",
                    {"file": str(self.mechanisms.flagd.path)},
                )
            current = variants[flag]
            wanted = str(normalized.params["variant"])
            if current == wanted:
                raise ApiError(
                    409,
                    "fault_already_applied",
                    f"{flag} is already at '{wanted}'; revert it before injecting again",
                )
            pre.update({"flag": flag, "before": current, "after": wanted})
        elif mechanism == Mechanism.CONTAINER_STOP:
            if target.state != "running":
                raise ApiError(
                    409,
                    "target_not_running",
                    f"{target.container} is '{target.state}'; nothing to stop",
                )
            pre["before_state"] = target.state
        elif mechanism == Mechanism.CONTAINER_PAUSE:
            if target.paused:
                raise ApiError(409, "target_paused", f"{target.container} is already paused")
        elif mechanism == Mechanism.NETWORK_PARTITION:
            if not target.networks:
                raise ApiError(409, "target_detached", f"{target.container} has no network")
        elif mechanism == Mechanism.RESOURCE_LIMIT:
            pre["before"] = {
                "cpu_limit_cores": target.cpu_limit_cores,
                "memory_limit_mb": target.memory_limit_mb,
                "memory_swap_mb": target.memory_swap_mb,
            }
            pre["applied"] = {
                key: value
                for key, value in normalized.params.items()
                if key in ("cpus", "memory_mb")
            }
        elif mechanism == Mechanism.LOAD_SPIKE:
            payload = await self.mechanisms.locust.get_json("/stats/requests")
            pre["before"] = {"users": int(payload.get("user_count") or 0)}
        return pre

    async def _baseline(self, definition: FaultDefinition, target_name: str) -> float | None:
        spec = self._symptom_for(definition, target_name)
        try:
            reading = await self.probe.read(spec, container=target_name)
        except Exception as exc:  # a missing baseline degrades the observer, not the injection
            log.warning("fault_baseline_failed", fault=definition.id, error=str(exc))
            return None
        return reading.value

    @staticmethod
    def _symptom_for(definition: FaultDefinition, target_name: str) -> SymptomSpec:
        if definition.category == FaultCategory.INFRASTRUCTURE:
            return definition.symptom.model_copy(update={"service": target_name})
        return definition.symptom

    # --- inject ------------------------------------------------------------
    async def inject(self, request: FaultRequest) -> InjectionResult:
        normalized = resolve_request(request)
        definition = normalized.definition
        target = await resolve_target(normalized.target)

        async with self._lock:
            pre_image = await self._preflight(normalized, target)
            baseline = await self._baseline(definition, normalized.target)
            plan = {
                "fault_id": definition.id,
                "label": definition.label,
                "target": normalized.target,
                "params": normalized.params,
                "ttl_s": normalized.ttl_s,
                "mechanism": definition.mechanism.value,
                "safety": {
                    "project": target.project,
                    "container": target.container,
                    "network": target.network or None,
                    "aliases": target.network_aliases,
                    "cpu_limit_cores": target.cpu_limit_cores,
                    "memory_limit_mb": target.memory_limit_mb,
                },
                "pre_image": pre_image,
                "baseline": baseline,
                "symptom": definition.symptom.model_dump(),
            }
            if request.dry_run:
                async with SessionFactory() as session:
                    await self._check_conflict(session, definition, normalized.target)
                return InjectionResult(dry_run=True, plan=plan)

            async with SessionFactory() as session:
                await self._check_conflict(session, definition, normalized.target)
                now = datetime.now(UTC)
                row = Fault(
                    type=definition.id,
                    target=normalized.target,
                    params=normalized.params,
                    status=FaultStatus.PROPAGATING.value,
                    injected_at=now,
                    expires_at=now + timedelta(seconds=normalized.ttl_s),
                    actor=(request.actor or "operator")[:ACTOR_MAX],
                    injection_ref={"plan": plan, "pre_image": pre_image, "baseline": baseline},
                )
                session.add(row)
                await session.commit()
                await session.refresh(row)
                row_id = row.id

            try:
                outcome = await self._apply(definition, normalized, target)
            except Exception as exc:
                await self._best_effort_undo(definition, target, pre_image)
                await self._fail_injection(row_id, definition, normalized, exc, actor=request.actor)
                raise

            direct = definition.mechanism != Mechanism.FLAGD
            status = FaultStatus.ACTIVE if direct else FaultStatus.PROPAGATING
            observed = None
            if direct:
                reading = await self._read_symptom(definition, normalized.target)
                observed = self._observation(reading, baseline)

            async with SessionFactory() as session:
                live = await session.get(Fault, row_id)
                if live is None:  # pragma: no cover - impossible inside the lock
                    raise ApiError(500, "fault_lost", "Fault row disappeared during injection")
                ref = dict(live.injection_ref or {})
                ref["injection"] = outcome.ref
                ref["detail"] = outcome.detail
                if observed is not None:
                    ref["observed"] = observed
                live.injection_ref = ref
                live.status = status.value
                await session.commit()
                await session.refresh(live)
                view = _view(live)

            await self._audit(
                AuditEntry(
                    actor=view.actor,
                    action="inject",
                    fault_row_id=view.id,
                    fault_id=view.fault_id,
                    target=view.target,
                    outcome=view.status.value,
                    detail=outcome.detail,
                    before=pre_image,
                    after=outcome.ref,
                )
            )
            bus.publish(
                "fault.injected",
                {
                    "id": view.id,
                    "fault_id": view.fault_id,
                    "target": view.target,
                    "status": view.status.value,
                    "expires_at": view.expires_at.isoformat() if view.expires_at else None,
                    "detail": outcome.detail,
                },
            )
            log.info(
                "fault_injected",
                fault_row=view.id,
                fault=view.fault_id,
                target=view.target,
                status=view.status.value,
            )
            return InjectionResult(dry_run=False, fault=view)

    async def _apply(
        self,
        definition: FaultDefinition,
        normalized: NormalizedFault,
        target: ContainerTarget,
    ) -> MechanismOutcome:
        mechanism = definition.mechanism
        if mechanism == Mechanism.FLAGD:
            return await self.mechanisms.inject_flagd(
                definition.flag or "", str(normalized.params["variant"])
            )
        if mechanism == Mechanism.CONTAINER_STOP:
            return await self.mechanisms.inject_stop(target)
        if mechanism == Mechanism.CONTAINER_PAUSE:
            return await self.mechanisms.inject_pause(target)
        if mechanism == Mechanism.NETWORK_PARTITION:
            return await self.mechanisms.inject_partition(target)
        if mechanism == Mechanism.RESOURCE_LIMIT:
            return await self.mechanisms.inject_limits(target, normalized.params)
        if mechanism == Mechanism.LOAD_SPIKE:
            return await self.mechanisms.inject_load(target, normalized.params)
        raise ApiError(422, "unsupported_mechanism", str(mechanism))  # pragma: no cover

    async def _check_conflict(self, session: Any, definition: FaultDefinition, target: str) -> None:
        rows = (
            (
                await session.execute(
                    select(Fault).where(
                        Fault.status.in_([status.value for status in OPEN_STATUSES])
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            same_fault = row.type == definition.id and (
                definition.category == FaultCategory.APPLICATION or row.target == target
            )
            same_infra_target = (
                definition.category == FaultCategory.INFRASTRUCTURE
                and row.target == target
                and row.type != definition.id
            )
            if same_fault or same_infra_target:
                raise ApiError(
                    409,
                    "fault_conflict",
                    f"{row.type} on {row.target} is already {row.status} (fault {row.id})",
                    {"existing_fault_id": row.id, "existing_status": row.status},
                )

    async def _fail_injection(
        self,
        row_id: int,
        definition: FaultDefinition,
        normalized: NormalizedFault,
        exc: Exception,
        *,
        actor: str,
    ) -> None:
        detail = str(exc)[:400]
        await self._audit(
            AuditEntry(
                actor=(actor or "operator")[:ACTOR_MAX],
                action="inject",
                fault_row_id=row_id,
                fault_id=definition.id,
                target=normalized.target,
                outcome=FaultStatus.FAILED.value,
                detail=detail,
            )
        )
        async with SessionFactory() as session:
            row = await session.get(Fault, row_id)
            if row is not None:
                ref = dict(row.injection_ref or {})
                ref["error"] = detail
                row.injection_ref = ref
                row.status = FaultStatus.FAILED.value
                await session.commit()
        bus.publish(
            "fault.failed",
            {"id": row_id, "fault_id": definition.id, "detail": detail},
        )

    async def _best_effort_undo(
        self, definition: FaultDefinition, target: ContainerTarget, pre_image: dict[str, Any]
    ) -> None:
        try:
            if await self._is_reverted(definition, target, pre_image):
                return
            outcome = await self._revert_mechanism(definition, target, pre_image)
            log.warning(
                "fault_injection_undone",
                fault=definition.id,
                target=target.service,
                detail=outcome.detail,
            )
        except Exception as exc:
            log.error("fault_undo_failed", fault=definition.id, error=str(exc))

    # --- observation -------------------------------------------------------
    async def _read_symptom(self, definition: FaultDefinition, target: str) -> ProbeReading | None:
        spec = self._symptom_for(definition, target)
        try:
            return await self.probe.read(spec, container=target)
        except Exception as exc:
            log.warning("fault_probe_failed", fault=definition.id, error=str(exc))
            return None

    @staticmethod
    def _observation(reading: ProbeReading | None, baseline: float | None) -> dict[str, Any]:
        return {
            "at": datetime.now(UTC).isoformat(),
            "value": reading.value if reading else None,
            "detail": reading.detail if reading else "no reading",
            "baseline": baseline,
        }

    async def observe_once(self) -> list[FaultView]:
        """Promote PROPAGATING faults whose symptom has appeared."""
        promoted: list[FaultView] = []
        async with SessionFactory() as session:
            rows = (
                (
                    await session.execute(
                        select(Fault).where(Fault.status == FaultStatus.PROPAGATING.value)
                    )
                )
                .scalars()
                .all()
            )
        for row in rows:
            definition = DEFINITIONS.get(row.type)
            if definition is None:
                continue
            # Direct mechanisms are ACTIVE from the read-back; only a flagd
            # write has a propagation window to observe.
            if definition.mechanism != Mechanism.FLAGD:
                continue
            ref = dict(row.injection_ref or {})
            baseline = ref.get("baseline")
            reading = await self._read_symptom(definition, row.target)
            if reading is None:
                continue
            spec = self._symptom_for(definition, row.target)
            if not symptom_satisfied(spec, reading, baseline):
                continue
            async with SessionFactory() as session:
                live = await session.get(Fault, row.id)
                if live is None or live.status != FaultStatus.PROPAGATING.value:
                    continue
                live_ref = dict(live.injection_ref or {})
                live_ref["observed"] = self._observation(reading, baseline)
                live.injection_ref = live_ref
                live.status = FaultStatus.ACTIVE.value
                await session.commit()
                await session.refresh(live)
                view = _view(live)
            promoted.append(view)
            await self._audit(
                AuditEntry(
                    actor="watchdog",
                    action="observe",
                    fault_row_id=view.id,
                    fault_id=view.fault_id,
                    target=view.target,
                    outcome=view.status.value,
                    detail=reading.detail,
                    after={"value": reading.value, "baseline": baseline},
                )
            )
            bus.publish(
                "fault.active",
                {
                    "id": view.id,
                    "fault_id": view.fault_id,
                    "target": view.target,
                    "status": view.status.value,
                    "detail": reading.detail,
                    "value": reading.value,
                    "baseline": baseline,
                },
            )
            log.info(
                "fault_active",
                fault_row=view.id,
                fault=view.fault_id,
                target=view.target,
                value=reading.value,
            )
        return promoted

    # --- revert ------------------------------------------------------------
    async def revert(
        self, row_id: int, *, actor: str = "operator", reason: str = "manual"
    ) -> FaultView:
        async with self._lock:
            async with SessionFactory() as session:
                row = await session.get(Fault, row_id)
            if row is None:
                raise ApiError(404, "fault_not_found", f"Fault {row_id} does not exist")
            if row.status in {status.value for status in CLOSED_STATUSES}:
                return _view(row)  # idempotent
            definition = DEFINITIONS.get(row.type)
            if definition is None:
                raise ApiError(409, "fault_definition_missing", f"unknown fault type {row.type}")
            ref = dict(row.injection_ref or {})
            pre_image = dict(ref.get("pre_image") or {})

            async with SessionFactory() as session:
                live = await session.get(Fault, row_id)
                if live is None:
                    raise ApiError(404, "fault_not_found", f"Fault {row_id} does not exist")
                live.status = FaultStatus.REVERTING.value
                await session.commit()

            try:
                target = await resolve_target(
                    row.target,
                    require_network=definition.mechanism != Mechanism.NETWORK_PARTITION,
                )
                if await self._is_reverted(definition, target, pre_image):
                    outcome = MechanismOutcome(
                        ref={"already": True, "mechanism": definition.mechanism.value},
                        detail="state already restored; no command issued",
                    )
                else:
                    outcome = await self._revert_mechanism(definition, target, pre_image)
            except Exception as exc:
                await self._fail_revert(
                    row_id, definition, actor, exc, reason=reason, target_name=row.target
                )
                raise

            final = FaultStatus.EXPIRED if reason in ("ttl", "expired") else FaultStatus.REVERTED
            reset_at = datetime.now(UTC)
            async with SessionFactory() as session:
                live = await session.get(Fault, row_id)
                if live is None:  # pragma: no cover
                    raise ApiError(404, "fault_not_found", f"Fault {row_id} does not exist")
                ref = dict(live.injection_ref or {})
                ref["revert"] = {
                    **outcome.ref,
                    "at": reset_at.isoformat(),
                    "actor": actor,
                    "reason": reason,
                    "verified": True,
                }
                live.injection_ref = ref
                live.status = final.value
                live.reset_at = reset_at
                await session.commit()
                await session.refresh(live)
                view = _view(live)

            await self._audit(
                AuditEntry(
                    actor=actor,
                    action="revert",
                    fault_row_id=view.id,
                    fault_id=view.fault_id,
                    target=view.target,
                    outcome=view.status.value,
                    detail=outcome.detail,
                    before=pre_image,
                    after=outcome.ref,
                )
            )
            event = "fault.expired" if final == FaultStatus.EXPIRED else "fault.reverted"
            bus.publish(
                event,
                {
                    "id": view.id,
                    "fault_id": view.fault_id,
                    "target": view.target,
                    "status": view.status.value,
                    "reason": reason,
                    "detail": outcome.detail,
                },
            )
            log.info(
                "fault_reverted",
                fault_row=view.id,
                fault=view.fault_id,
                target=view.target,
                reason=reason,
                status=view.status.value,
            )
            return view

    async def _revert_mechanism(
        self, definition: FaultDefinition, target: ContainerTarget, pre_image: dict[str, Any]
    ) -> MechanismOutcome:
        mechanism = definition.mechanism
        if mechanism == Mechanism.FLAGD:
            return await self.mechanisms.revert_flagd(
                {"flag": pre_image.get("flag"), "before": pre_image.get("before")}
            )
        if mechanism == Mechanism.CONTAINER_STOP:
            return await self.mechanisms.revert_stop(target, pre_image)
        if mechanism == Mechanism.CONTAINER_PAUSE:
            return await self.mechanisms.revert_pause(target, pre_image)
        if mechanism == Mechanism.NETWORK_PARTITION:
            return await self.mechanisms.revert_partition(target, pre_image)
        if mechanism == Mechanism.RESOURCE_LIMIT:
            return await self.mechanisms.revert_limits(target, pre_image)
        if mechanism == Mechanism.LOAD_SPIKE:
            return await self.mechanisms.revert_load(target, pre_image)
        raise ApiError(422, "unsupported_mechanism", str(mechanism))  # pragma: no cover

    async def _is_reverted(
        self, definition: FaultDefinition, target: ContainerTarget, pre_image: dict[str, Any]
    ) -> bool:
        mechanism = definition.mechanism
        if mechanism == Mechanism.FLAGD:
            current = await asyncio.to_thread(self.mechanisms.flagd.current_variants)
            return current.get(str(pre_image.get("flag"))) == pre_image.get("before")
        if mechanism == Mechanism.LOAD_SPIKE:
            payload = await self.mechanisms.locust.get_json("/stats/requests")
            expected = int((pre_image.get("before") or {}).get("users") or 0)
            return int(payload.get("user_count") or 0) == expected
        try:
            item = await self._inspect_or_none(target.container)
            if item is None:
                return True  # container was recreated: the fault is gone
            state: dict[str, Any] = {
                "running": bool((item.get("State") or {}).get("Running")),
                "paused": bool((item.get("State") or {}).get("Paused")),
                "networks": sorted(
                    (((item.get("NetworkSettings") or {}).get("Networks")) or {}).keys()
                ),
                "cpu_limit_cores": (
                    round(int((item.get("HostConfig") or {}).get("NanoCpus") or 0) / 1e9, 3) or None
                ),
                "memory_limit_mb": (
                    round(int((item.get("HostConfig") or {}).get("Memory") or 0) / 1024 / 1024)
                    or None
                ),
            }
        except DockerError:
            raise
        if mechanism == Mechanism.CONTAINER_STOP:
            return bool(state["running"])
        if mechanism == Mechanism.CONTAINER_PAUSE:
            return not bool(state["paused"])
        if mechanism == Mechanism.NETWORK_PARTITION:
            return settings.demo_network in state["networks"]
        if mechanism == Mechanism.RESOURCE_LIMIT:
            before = pre_image.get("before") or {}
            applied = pre_image.get("applied") or {}
            if "cpus" in applied:
                expected_cpu = before.get("cpu_limit_cores")
                current = state["cpu_limit_cores"]
                if expected_cpu is None:
                    # A cleared limit is implemented as NCPU (see revert_limits).
                    if current != await host_cores():
                        return False
                elif current != expected_cpu:
                    return False
            return not (
                "memory_mb" in applied and state["memory_limit_mb"] != before.get("memory_limit_mb")
            )
        return False

    async def _inspect_or_none(self, name: str) -> dict[str, Any] | None:
        try:
            items = await docker_inspect(name, timeout_s=10.0)
        except DockerError as exc:
            if "no such" in str(exc).lower():
                return None
            raise
        return items[0] if items else None

    async def _fail_revert(
        self,
        row_id: int,
        definition: FaultDefinition,
        actor: str,
        exc: Exception,
        *,
        reason: str,
        target_name: str,
    ) -> None:
        detail = str(exc)[:400]
        attempts = 0
        async with SessionFactory() as session:
            row = await session.get(Fault, row_id)
            if row is not None:
                ref = dict(row.injection_ref or {})
                attempts = int(ref.get("revert_attempts") or 0) + 1
                ref["revert_attempts"] = attempts
                ref["revert_error"] = detail
                row.injection_ref = ref
                # Stay retryable unless the mechanism keeps failing; the
                # watchdog would otherwise hammer Docker every two seconds.
                row.status = (
                    FaultStatus.FAILED.value
                    if attempts >= REVERT_ATTEMPT_LIMIT
                    else FaultStatus.ACTIVE.value
                )
                await session.commit()
        await self._audit(
            AuditEntry(
                actor=actor,
                action="revert",
                fault_row_id=row_id,
                fault_id=definition.id,
                target=target_name,
                outcome="error",
                detail=f"attempt {attempts}: {detail}",
            )
        )
        bus.publish(
            "fault.revert_failed",
            {"id": row_id, "fault_id": definition.id, "attempt": attempts, "detail": detail},
        )
        log.error("fault_revert_failed", fault_row=row_id, attempt=attempts, error=detail)
        if isinstance(exc, ApiError):
            raise exc
        raise ApiError(502, "fault_revert_failed", detail) from exc

    # --- watchdog / reset --------------------------------------------------
    async def expire_due(self) -> list[FaultView]:
        now = datetime.now(UTC)
        async with SessionFactory() as session:
            rows = (
                (
                    await session.execute(
                        select(Fault).where(
                            Fault.status.in_([status.value for status in OPEN_STATUSES]),
                            Fault.expires_at.is_not(None),
                            Fault.expires_at <= now,
                        )
                    )
                )
                .scalars()
                .all()
            )
        reverted: list[FaultView] = []
        for row in rows:
            try:
                reverted.append(await self.revert(row.id, actor="watchdog", reason="ttl"))
            except ApiError as exc:
                log.error("fault_expiry_failed", fault_row=row.id, error=exc.message)
        return reverted

    async def reset_all(self, *, actor: str = "operator", sweep_flags: bool = True) -> ResetReport:
        async with SessionFactory() as session:
            rows = (
                (
                    await session.execute(
                        select(Fault).where(
                            Fault.status.in_([status.value for status in OPEN_STATUSES])
                        )
                    )
                )
                .scalars()
                .all()
            )
            row_ids = [row.id for row in rows]
        reverted: list[FaultView] = []
        failed: list[dict[str, Any]] = []
        for row_id in row_ids:
            try:
                reverted.append(await self.revert(row_id, actor=actor, reason="reset_all"))
            except ApiError as exc:
                failed.append({"id": row_id, "error": exc.code, "message": exc.message})
        drift: list[dict[str, Any]] = []
        if sweep_flags:
            try:
                drift = await reset_drift(self.mechanisms.flagd)
            except Exception as exc:
                failed.append({"id": None, "error": "flag_drift", "message": str(exc)[:300]})
        report = ResetReport(
            reverted=reverted,
            failed=failed,
            flag_drift=drift,
            generated_at=datetime.now(UTC),
        )
        await self._audit(
            AuditEntry(
                actor=actor,
                action="reset_all",
                outcome="ok" if not failed else "partial",
                detail=f"{len(reverted)} reverted, {len(drift)} flags reset, {len(failed)} failed",
                after=report.model_dump(mode="json"),
            )
        )
        bus.publish(
            "fault.reset_all",
            {
                "reverted": len(reverted),
                "flag_drift": len(drift),
                "failed": len(failed),
                "actor": actor,
            },
        )
        log.info("fault_reset_all", reverted=len(reverted), drift=len(drift), failed=len(failed))
        return report

    async def reconcile(self) -> list[dict[str, Any]]:
        """Startup reconciliation after a crash or restart.

        - injection gone (someone reverted it, or the API died between the row
          insert and the side effect) → mark REVERTED, no command.
        - expired while the API was down → revert now.
        - still present and unexpired → resume tracking; the watchdog owns it.
        """
        now = datetime.now(UTC)
        async with SessionFactory() as session:
            rows = (
                (
                    await session.execute(
                        select(Fault).where(
                            Fault.status.in_([status.value for status in OPEN_STATUSES])
                        )
                    )
                )
                .scalars()
                .all()
            )
        actions: list[dict[str, Any]] = []
        for row in rows:
            definition = DEFINITIONS.get(row.type)
            if definition is None:
                continue
            expires_at = _as_utc(row.expires_at) if row.expires_at else None
            if expires_at is not None and expires_at <= now:
                try:
                    view = await self.revert(row.id, actor="watchdog", reason="ttl")
                    actions.append(
                        {"id": row.id, "action": "expired_on_start", "status": view.status}
                    )
                except ApiError as exc:
                    actions.append({"id": row.id, "action": "expire_failed", "error": exc.message})
                continue
            target = await resolve_target(
                row.target, require_network=definition.mechanism != Mechanism.NETWORK_PARTITION
            )
            pre_image = dict((row.injection_ref or {}).get("pre_image") or {})
            try:
                reverted = await self._is_reverted(definition, target, pre_image)
            except Exception as exc:
                actions.append(
                    {"id": row.id, "action": "reconcile_probe_failed", "error": str(exc)}
                )
                continue
            if reverted:
                async with SessionFactory() as session:
                    live = await session.get(Fault, row.id)
                    if live is None:
                        continue
                    ref = dict(live.injection_ref or {})
                    ref["drift"] = {
                        "at": now.isoformat(),
                        "detail": "injection was absent at startup",
                    }
                    live.injection_ref = ref
                    live.status = FaultStatus.REVERTED.value
                    live.reset_at = now
                    await session.commit()
                actions.append({"id": row.id, "action": "marked_reverted_drift"})
                await self._audit(
                    AuditEntry(
                        actor="startup",
                        action="reconcile",
                        fault_row_id=row.id,
                        fault_id=row.type,
                        target=row.target,
                        outcome="REVERTED",
                        detail="injection absent at startup; marked reverted",
                    )
                )
                bus.publish(
                    "fault.reconciled",
                    {"id": row.id, "fault_id": row.type, "action": "marked_reverted_drift"},
                )
            else:
                actions.append({"id": row.id, "action": "resumed", "status": row.status})
                bus.publish(
                    "fault.reconciled",
                    {"id": row.id, "fault_id": row.type, "action": "resumed"},
                )
        if actions:
            log.info("fault_reconciled", actions=actions)
        return actions

    async def _audit(self, entry: AuditEntry) -> None:
        try:
            await self.audit.append(entry)
        except Exception as exc:  # the audit file must never break a revert
            log.error("fault_audit_failed", error=str(exc))


_controller: FaultController | None = None


def get_controller() -> FaultController:
    global _controller
    if _controller is None:
        _controller = FaultController(mechanisms=Mechanisms(flagd=FlagdWriter()))
    return _controller


def reset_controller_for_tests() -> None:
    global _controller
    _controller = None


__all__ = [
    "FaultController",
    "FaultList",
    "FaultView",
    "InjectionResult",
    "ResetReport",
    "get_controller",
    "reset_controller_for_tests",
]
