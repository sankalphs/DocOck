"""Desired state and drift: what "healthy" looks like, and how far the lab is from it.

Reverting a recorded fault only fixes faults DocOck injected itself. A container
someone stopped by hand, a flag flipped in flagd-ui, a `docker update` from a
shell, a crash loop — none of those has a fault row. The drift scanner makes
remediation independent of *how* the lab broke:

    desired state (learned + declared)  vs  live state (Docker, flagd, locust)
                         → drift items, each with the catalog action that fixes it

Desired state has three sources, from most to least authoritative:

- **Declared**: every allowlisted container should run, unpaused, attached to
  the demo network; every flag should sit on its upstream seed default; the
  load generator should run its configured user count.
- **Recorded**: an open fault's pre-image (the limits, aliases and variant it
  replaced) is the exact state before the fault.
- **Learned**: limits and network aliases observed on healthy containers. A
  limit is a *high-water mark* — raising a limit is never a fault, so a learned
  limit only ever goes up — and nothing is learned for a target that has an
  open fault.

Everything here is read-only except `DesiredStateStore.save`, which writes
`runtime/desired-state.json`.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import ROOT_DIR, settings
from app.faults.catalog import (
    ALLOWED_TARGETS,
    DEFINITIONS,
    DEPENDENCIES,
    LOAD_TARGETS,
    SERVICE_TARGETS,
)
from app.logging import get_logger
from app.telemetry.docker import DockerError, docker_inspect, docker_json, host_cores

log = get_logger(__name__)

PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"

DriftKind = Literal[
    "container_missing",
    "container_stopped",
    "container_crashed",
    "container_restarting",
    "container_paused",
    "container_unhealthy",
    "network_detached",
    "cpu_limited",
    "memory_limited",
    "flag_on",
    "load_changed",
]

# Lower is more causal: a stopped container explains its unhealthy caller, a
# limit explains a crash loop, a flag explains an error rate.
KIND_PRIORITY: dict[str, int] = {
    "container_missing": 0,
    "memory_limited": 1,
    "cpu_limited": 2,
    "container_stopped": 3,
    "container_crashed": 3,
    "container_restarting": 3,
    "container_paused": 3,
    "network_detached": 4,
    "flag_on": 5,
    "load_changed": 6,
    "container_unhealthy": 7,
}

# Diagnosis vocabulary (agent/schemas.py FaultClass) for each drift kind.
KIND_FAULT_CLASS: dict[str, str] = {
    "container_missing": "dependency_unavailable",
    "container_stopped": "dependency_unavailable",
    "container_crashed": "dependency_unavailable",
    "container_restarting": "dependency_unavailable",
    "container_paused": "dependency_unavailable",
    "container_unhealthy": "application_error",
    "network_detached": "network",
    "cpu_limited": "resource_exhaustion",
    "memory_limited": "resource_exhaustion",
    "flag_on": "application_error",
    "load_changed": "traffic",
}

# The flag that floods the frontend is a traffic problem, not an app error.
TRAFFIC_FLAGS = frozenset({"loadGeneratorFloodHomepage"})
DEFAULT_LOAD_USERS = 5


def flag_owners() -> dict[str, str]:
    """flag key -> the service a catalog fault on it targets."""
    owners: dict[str, str] = {}
    for definition in DEFINITIONS.values():
        if definition.flag:
            owners.setdefault(definition.flag, definition.target)
    return owners


def callers_of(dependency: str) -> list[str]:
    return sorted(service for service, deps in DEPENDENCIES.items() if dependency in deps)


class DesiredContainer(BaseModel):
    service: str
    network: str | None = None
    aliases: list[str] = Field(default_factory=list)
    # None = unlimited. High-water marks (see module docstring).
    cpu_limit_cores: float | None = None
    memory_limit_mb: int | None = None
    memory_swap_mb: int | None = None
    # False until limits were observed on a healthy container (or recorded by a
    # fault pre-image); until then limits are not compared at all.
    limits_learned: bool = False
    learned_at: datetime | None = None


class DesiredState(BaseModel):
    version: int = 1
    containers: dict[str, DesiredContainer] = Field(default_factory=dict)
    flags: dict[str, str] = Field(default_factory=dict)
    load_users: int | None = None
    updated_at: datetime | None = None


class DriftFix(BaseModel):
    action_id: str
    target: str


class DriftItem(BaseModel):
    kind: DriftKind
    # The component that drifted: a service/container, or "flag:<key>".
    service: str
    component: str
    observed: Any = None
    desired: Any = None
    detail: str
    fix: DriftFix | None = None
    fault_class: str = "unknown"
    priority: int = 9
    # Services whose symptoms this drift explains (itself + its callers).
    explains: list[str] = Field(default_factory=list)

    def key(self) -> tuple[str, str]:
        return (self.kind, self.component)


class DriftReport(BaseModel):
    at: datetime
    items: list[DriftItem] = Field(default_factory=list)
    scanned: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    desired_updated_at: datetime | None = None

    @property
    def clean(self) -> bool:
        return not self.items

    def for_target(self, target: str) -> list[DriftItem]:
        return [item for item in self.items if item.service == target]

    def summary(self, limit: int = 12) -> list[dict[str, Any]]:
        return [
            {
                "kind": item.kind,
                "service": item.service,
                "detail": item.detail,
                "fix": item.fix.model_dump() if item.fix else None,
                "explains": item.explains,
            }
            for item in self.items[:limit]
        ]


def _seed_flag_defaults() -> dict[str, str]:
    """Upstream defaultVariant per flag; every fault flag defaults to off."""
    seed = ROOT_DIR / "opentelemetry-demo" / "src" / "flagd" / "demo.flagd.json"
    defaults: dict[str, str] = {}
    try:
        raw = seed.read_bytes().lstrip(b"\xef\xbb\xbf")
        from app.faults.flagd import effective_variant

        for key, flag in (json.loads(raw.decode("utf-8")).get("flags") or {}).items():
            defaults[str(key)] = effective_variant(flag)
    except (OSError, ValueError):
        pass
    return defaults


class DesiredStateStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings.desired_state_file
        self.state = DesiredState()
        self._loaded = False

    def load(self) -> DesiredState:
        if self._loaded:
            return self.state
        self._loaded = True
        try:
            self.state = DesiredState.model_validate_json(self.path.read_text("utf-8"))
        except FileNotFoundError:
            self.state = DesiredState()
        except (OSError, ValueError) as exc:
            log.warning("desired_state_unreadable", path=str(self.path), error=str(exc))
            self.state = DesiredState()
        seed = _seed_flag_defaults()
        for key, variant in seed.items():
            self.state.flags.setdefault(key, variant)
        return self.state

    def save(self) -> None:
        self.state.updated_at = datetime.now(UTC)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(self.state.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def desired_flag(self, key: str) -> str:
        return self.load().flags.get(key, "off")

    def desired_users(self) -> int:
        return self.load().load_users or DEFAULT_LOAD_USERS

    # --- learning ---------------------------------------------------------
    def learn_container(
        self,
        service: str,
        live: dict[str, Any],
        *,
        unlimited_cpu: int,
        pre_image: dict[str, Any] | None = None,
        limits: bool = True,
    ) -> bool:
        """Fold one healthy observation in. Returns True when something changed.

        `pre_image` is an open fault's recorded "before" limits: authoritative,
        and the live limits are then *not* learned (they are the fault).
        """
        state = self.load()
        entry = state.containers.get(service)
        changed = False
        if entry is None:
            entry = DesiredContainer(service=service)
            state.containers[service] = entry
            changed = True
        if live.get("networks"):
            network = live["networks"][0]
            if entry.network != network:
                entry.network, changed = network, True
            for alias in live.get("aliases") or []:
                if alias not in entry.aliases and len(alias) < 64:
                    entry.aliases.append(alias)
                    changed = True
        if pre_image is not None:
            for key in ("cpu_limit_cores", "memory_limit_mb", "memory_swap_mb"):
                if key in pre_image and getattr(entry, key) != pre_image[key]:
                    setattr(entry, key, pre_image[key])
                    changed = True
            if not entry.limits_learned:
                entry.limits_learned, changed = True, True
        elif limits:
            cpu = live.get("cpu_limit_cores")
            if cpu is not None and unlimited_cpu and cpu >= unlimited_cpu:
                cpu = None  # Docker cannot clear a CPU cap; NCPU means unlimited
            fresh = not entry.limits_learned
            changed |= self._raise(entry, "cpu_limit_cores", cpu, fresh)
            changed |= self._raise(entry, "memory_limit_mb", live.get("memory_limit_mb"), fresh)
            changed |= self._raise(entry, "memory_swap_mb", live.get("memory_swap_mb"), fresh)
            if fresh:
                entry.limits_learned, changed = True, True
        if changed:
            entry.learned_at = datetime.now(UTC)
        return changed

    @staticmethod
    def _raise(entry: DesiredContainer, key: str, value: Any, fresh: bool) -> bool:
        current = getattr(entry, key)
        if fresh:
            if current != value:
                setattr(entry, key, value)
                return True
            return False
        if current is None:
            return False  # already unlimited; a lower limit now is drift, not news
        if value is None or value > current:
            setattr(entry, key, value)
            return True
        return False

    def learn_users(self, users: int) -> bool:
        state = self.load()
        if users > 0 and state.load_users is None:
            state.load_users = users
            return True
        return False


def _container_view(item: dict[str, Any]) -> dict[str, Any]:
    state = item.get("State") or {}
    host = item.get("HostConfig") or {}
    attachments = ((item.get("NetworkSettings") or {}).get("Networks")) or {}
    networks = sorted(attachments)
    aliases: list[str] = []
    for name in networks:
        attachment = attachments.get(name) or {}
        for alias in (attachment.get("Aliases") or []) + (attachment.get("DNSNames") or []):
            short_id = str(item.get("Id") or "-")[:12]
            if alias and alias not in aliases and not str(alias).startswith(short_id):
                aliases.append(str(alias))
    nano = int(host.get("NanoCpus") or 0)
    memory = int(host.get("Memory") or 0)
    swap = int(host.get("MemorySwap") or 0)
    labels = (item.get("Config") or {}).get("Labels") or {}
    return {
        "name": str(item.get("Name") or "").lstrip("/"),
        "service": str(labels.get(SERVICE_LABEL) or ""),
        "status": str(state.get("Status") or "unknown"),
        "running": bool(state.get("Running")),
        "paused": bool(state.get("Paused")),
        "restarting": bool(state.get("Restarting")),
        "exit_code": int(state.get("ExitCode") or 0),
        "oom_killed": bool(state.get("OOMKilled")),
        "restart_count": int(item.get("RestartCount") or 0),
        "health": str((state.get("Health") or {}).get("Status") or "none"),
        "networks": networks,
        "aliases": aliases,
        "cpu_limit_cores": round(nano / 1e9, 3) if nano else None,
        "memory_limit_mb": round(memory / 1024 / 1024) if memory else None,
        "memory_swap_mb": round(swap / 1024 / 1024) if swap > 0 else None,
    }


async def live_containers() -> dict[str, dict[str, Any]]:
    """service -> container view, for every allowlisted container in the project."""
    rows = await docker_json(
        "ps",
        "-a",
        "--filter",
        f"label={PROJECT_LABEL}={settings.demo_compose_project}",
        "--format",
        "{{json .}}",
        timeout_s=10.0,
    )
    names = [str(row.get("Names") or "") for row in rows]
    names = [name for name in names if name]
    if not names:
        return {}
    items = await docker_inspect(*names, timeout_s=20.0)
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        view = _container_view(item)
        if view["service"] in ALLOWED_TARGETS:
            out[view["service"]] = view
    return out


def container_drift(
    service: str,
    live: dict[str, Any] | None,
    desired: DesiredContainer | None,
    *,
    unlimited_cpu: int,
    demo_network: str,
) -> list[DriftItem]:
    """Pure: every way one container differs from its desired state."""
    explains = [service, *callers_of(service)]

    def item(
        kind: DriftKind,
        detail: str,
        fix: tuple[str, str] | None,
        *,
        observed: Any = None,
        wanted: Any = None,
    ) -> DriftItem:
        return DriftItem(
            kind=kind,
            service=service,
            component=service,
            observed=observed,
            desired=wanted,
            detail=detail,
            fix=DriftFix(action_id=fix[0], target=fix[1]) if fix else None,
            fault_class=KIND_FAULT_CLASS[kind],
            priority=KIND_PRIORITY[kind],
            explains=explains,
        )

    if live is None:
        return [item("container_missing", f"no container for {service}", None)]
    out: list[DriftItem] = []
    limited = False
    if desired is not None and desired.limits_learned:
        cpu = live.get("cpu_limit_cores")
        if cpu is not None and unlimited_cpu and cpu >= unlimited_cpu:
            cpu = None
        if cpu is not None and (desired.cpu_limit_cores is None or cpu < desired.cpu_limit_cores):
            limited = True
            out.append(
                item(
                    "cpu_limited",
                    f"{service} CPU limit {cpu:g} cores, desired "
                    + (
                        "unlimited"
                        if desired.cpu_limit_cores is None
                        else f"{desired.cpu_limit_cores:g}"
                    ),
                    ("remove_resource_limit", service),
                    observed=cpu,
                    wanted=desired.cpu_limit_cores,
                )
            )
        memory = live.get("memory_limit_mb")
        if memory is not None and (
            desired.memory_limit_mb is None or memory < desired.memory_limit_mb
        ):
            limited = True
            out.append(
                item(
                    "memory_limited",
                    f"{service} memory limit {memory} MB, desired "
                    + (
                        "unlimited"
                        if desired.memory_limit_mb is None
                        else f"{desired.memory_limit_mb} MB"
                    )
                    + (" (OOM-killed)" if live.get("oom_killed") else ""),
                    ("remove_resource_limit", service),
                    observed=memory,
                    wanted=desired.memory_limit_mb,
                )
            )
    restart = ("restart_service", service)
    if live.get("restarting"):
        out.append(
            item(
                "container_restarting",
                f"{service} is restarting (restarts={live.get('restart_count')}, "
                f"exit {live.get('exit_code')}"
                + (", OOM-killed" if live.get("oom_killed") else "")
                + ")",
                None if limited else restart,
                observed=live.get("status"),
                wanted="running",
            )
        )
    elif not live.get("running"):
        crashed = int(live.get("exit_code") or 0) not in (0, 143) or live.get("oom_killed")
        out.append(
            item(
                "container_crashed" if crashed else "container_stopped",
                f"{service} is {live.get('status')} (exit {live.get('exit_code')}"
                + (", OOM-killed" if live.get("oom_killed") else "")
                + ")",
                None if limited else restart,
                observed=live.get("status"),
                wanted="running",
            )
        )
    elif live.get("paused"):
        out.append(item("container_paused", f"{service} is paused", restart, observed="paused"))
    if demo_network and demo_network not in (live.get("networks") or []):
        out.append(
            item(
                "network_detached",
                f"{service} is not attached to {demo_network}",
                ("restore_network", service),
                observed=live.get("networks") or [],
                wanted=[demo_network],
            )
        )
    if (
        live.get("running")
        and not live.get("paused")
        and live.get("health") == "unhealthy"
        and not out
    ):
        out.append(
            item(
                "container_unhealthy",
                f"{service} healthcheck reports unhealthy",
                restart,
                observed="unhealthy",
                wanted="healthy",
            )
        )
    return out


def flag_drift(current: dict[str, str], desired: dict[str, str]) -> list[DriftItem]:
    owners = flag_owners()
    out: list[DriftItem] = []
    for key, variant in sorted(current.items()):
        wanted = desired.get(key, "off")
        if variant == wanted or key not in owners:
            continue
        owner = owners[key]
        traffic = key in TRAFFIC_FLAGS
        out.append(
            DriftItem(
                kind="flag_on",
                service=owner,
                component=f"flag:{key}",
                observed=variant,
                desired=wanted,
                detail=f"flag {key}={variant} (desired {wanted}) affects {owner}",
                fix=DriftFix(
                    action_id="stop_load_generator" if traffic else "clear_feature_flag",
                    target="load-generator" if traffic else owner,
                ),
                fault_class="traffic" if traffic else "application_error",
                priority=KIND_PRIORITY["flag_on"],
                explains=[owner, *callers_of(owner)],
            )
        )
    return out


def load_drift(users: int | None, desired: int) -> list[DriftItem]:
    if users is None or users <= desired:
        return []
    return [
        DriftItem(
            kind="load_changed",
            service="load-generator",
            component="load-generator",
            observed=users,
            desired=desired,
            detail=f"load generator runs {users} users, desired {desired}",
            fix=DriftFix(action_id="stop_load_generator", target="load-generator"),
            fault_class="traffic",
            priority=KIND_PRIORITY["load_changed"],
            explains=["frontend", "frontend-proxy"],
        )
    ]


class DriftScanner:
    """Reads Docker, the flag file and locust; never writes the lab."""

    def __init__(self, store: DesiredStateStore | None = None) -> None:
        self.store = store or DesiredStateStore()
        self.last: DriftReport | None = None
        self._lock = asyncio.Lock()

    async def _users(self) -> int | None:
        from app.faults.controller import get_controller

        try:
            payload = await get_controller().mechanisms.locust.get_json("/stats/requests")
        except Exception:
            return None
        return int(payload.get("user_count") or 0)

    async def _flags(self) -> dict[str, str]:
        from app.faults.controller import get_controller

        return await asyncio.to_thread(get_controller().mechanisms.flagd.current_variants)

    async def _open_fault_pre_images(self) -> dict[str, dict[str, Any]]:
        """target -> recorded "before" limits of an open resource fault."""
        from sqlalchemy import select

        from app.db.models import Fault
        from app.db.session import SessionFactory
        from app.faults.catalog import OPEN_STATUSES

        out: dict[str, dict[str, Any]] = {"__open__": {}}
        try:
            async with SessionFactory() as session:
                rows = (
                    (
                        await session.execute(
                            select(Fault).where(Fault.status.in_([s.value for s in OPEN_STATUSES]))
                        )
                    )
                    .scalars()
                    .all()
                )
        except Exception:
            return out
        for row in rows:
            out["__open__"][row.target] = row.type
            pre = (row.injection_ref or {}).get("pre_image") or {}
            before = pre.get("before")
            if isinstance(before, dict) and "memory_limit_mb" in before:
                out[row.target] = before
        return out

    async def scan(self, *, services: list[str] | None = None, learn: bool = False) -> DriftReport:
        """Compare live state with desired state. `learn` folds healthy readings in."""
        async with self._lock:
            store = self.store
            store.load()
            report = DriftReport(at=datetime.now(UTC))
            try:
                unlimited = await host_cores()
            except Exception:
                unlimited = 0
            try:
                live = await live_containers()
            except (DockerError, OSError, ValueError) as exc:
                report.errors.append(f"docker: {exc}")
                live = {}
            faults = await self._open_fault_pre_images()
            open_targets = faults.pop("__open__", {})
            wanted = services or list(SERVICE_TARGETS)
            changed = False
            for service in wanted:
                if service in LOAD_TARGETS:
                    continue
                view = live.get(service)
                if view is None and not live:
                    continue  # Docker unreachable: unknown, not missing
                report.scanned.append(service)
                pre = faults.get(service)
                if learn and view is not None:
                    healthy = (
                        view["running"]
                        and not view["paused"]
                        and not view["restarting"]
                        and settings.demo_network in view["networks"]
                    )
                    if healthy or pre is not None:
                        changed |= store.learn_container(
                            service,
                            view,
                            unlimited_cpu=unlimited,
                            pre_image=pre,
                            # A target with an open fault teaches nothing about
                            # its limits except through the fault's pre-image.
                            limits=service not in open_targets,
                        )
                desired = store.state.containers.get(service)
                report.items.extend(
                    container_drift(
                        service,
                        view,
                        desired,
                        unlimited_cpu=unlimited,
                        demo_network=settings.demo_network,
                    )
                )
            scope = set(wanted)
            try:
                flags = await self._flags()
                for drift in flag_drift(flags, store.state.flags):
                    if (
                        services is None
                        or drift.service in scope
                        or (drift.fix is not None and drift.fix.target in scope)
                    ):
                        report.items.append(drift)
            except Exception as exc:
                report.errors.append(f"flagd: {exc}")
            if services is None or "load-generator" in scope:
                users = await self._users()
                if (
                    learn
                    and users
                    and not any(
                        kind in ("load_spike", "traffic_flood") for kind in open_targets.values()
                    )
                ):
                    changed |= store.learn_users(users)
                report.items.extend(load_drift(users, store.desired_users()))
            report.items.sort(key=lambda item: (item.priority, item.service, item.component))
            report.desired_updated_at = store.state.updated_at
            if changed:
                try:
                    store.save()
                except OSError as exc:
                    report.errors.append(f"save: {exc}")
            if services is None:
                self.last = report
            return report


scanner = DriftScanner()

__all__ = [
    "KIND_FAULT_CLASS",
    "DesiredContainer",
    "DesiredState",
    "DesiredStateStore",
    "DriftFix",
    "DriftItem",
    "DriftReport",
    "DriftScanner",
    "callers_of",
    "container_drift",
    "flag_drift",
    "flag_owners",
    "live_containers",
    "load_drift",
    "scanner",
]
