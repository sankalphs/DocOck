"""Fault mechanisms and symptom probes.

Two kinds of truth:

- *Direct* mechanisms (stop, pause, network, limits, load) change container state
  or the load generator. The change is read back immediately, so the fault is
  `ACTIVE` as soon as the read-back matches.
- *flagd* changes a shared config file. OFREP reflects it in ~0.5 s, but the demo
  services' SDK clients take 30-45 s to observe it (measured in P5). The fault
  stays `PROPAGATING` until a symptom probe sees the effect.

Every mutation takes a prepared `ContainerTarget` (already safety-checked) and
returns a JSON-serialisable pre/post image for the audit trail.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from pydantic import BaseModel, Field

from app.config import settings
from app.errors import ApiError
from app.faults.catalog import Mechanism, SymptomSpec
from app.faults.flagd import FlagdWriteError, FlagdWriter
from app.faults.safety import ContainerTarget
from app.logging import get_logger
from app.telemetry.docker import docker_inspect, docker_json, run_docker
from app.telemetry.http import AsyncHttpClient, shared_client
from app.telemetry.prometheus import PrometheusAdapter

log = get_logger(__name__)


class ProbeReading(BaseModel):
    value: float | None = None
    detail: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class MechanismOutcome(BaseModel):
    ref: dict[str, Any]
    detail: str


def _compare(value: float, comparator: str, threshold: float) -> bool:
    if comparator == ">":
        return value > threshold
    if comparator == ">=":
        return value >= threshold
    if comparator == "<":
        return value < threshold
    return value <= threshold


async def _inspect(name: str) -> dict[str, Any]:
    items = await docker_inspect(name, timeout_s=10.0)
    if not items:
        raise ApiError(502, "docker_error", f"container '{name}' disappeared")
    return items[0]


def _state(item: dict[str, Any]) -> dict[str, Any]:
    state = item.get("State") or {}
    health = str((state.get("Health") or {}).get("Status") or "none")
    return {
        "status": str(state.get("Status") or "unknown"),
        "running": bool(state.get("Running")),
        "paused": bool(state.get("Paused")),
        "health": health,
        "networks": sorted((((item.get("NetworkSettings") or {}).get("Networks")) or {}).keys()),
        "cpu_limit_cores": (
            round(int((item.get("HostConfig") or {}).get("NanoCpus") or 0) / 1e9, 3) or None
        ),
        "memory_limit_mb": (
            round(int((item.get("HostConfig") or {}).get("Memory") or 0) / 1024 / 1024) or None
        ),
    }


_cached_host_cores: int | None = None


async def host_cores() -> int:
    """`docker info` NCPU, cached. Used to make a CPU limit effectively unlimited.

    Docker cannot *clear* a CPU limit with `docker update` (`--cpus 0` is a
    no-op and the old NanoCPUs value survives), so reverting a container that
    had no limit raises the cap to the host core count instead.
    """
    global _cached_host_cores
    if _cached_host_cores is None:
        raw = await run_docker("info", "--format", "{{.NCPU}}", timeout_s=10.0)
        _cached_host_cores = max(1, int(raw.strip() or 1))
    return _cached_host_cores


async def wait_for_container(
    name: str,
    predicate: Any,
    *,
    timeout_s: float | None = None,
    interval_s: float = 0.3,
) -> dict[str, Any]:
    deadline = time.monotonic() + (timeout_s or settings.fault_revert_timeout_s)
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _state(await _inspect(name))
        if predicate(last):
            return last
        await asyncio.sleep(interval_s)
    raise ApiError(
        502,
        "state_not_reached",
        f"container '{name}' did not reach the expected state in time",
        {"last": last},
    )


class FaultProbe:
    """Reads the observable declared by a fault's `symptom`."""

    def __init__(self, prometheus: PrometheusAdapter | None = None) -> None:
        # The telemetry adapter caches for 10 s; the observer needs a fresh
        # reading every cycle, so it owns a zero-TTL instance.
        self._prometheus = prometheus or PrometheusAdapter(ttl_s=0)
        self._locust = shared_client(
            "load-generator", base_url=settings.load_generator_url, timeout_s=8.0
        )

    async def read(self, spec: SymptomSpec, *, container: str | None = None) -> ProbeReading:
        if spec.probe in ("error_rate", "p95_latency", "throughput"):
            result = await self._prometheus.metric(
                spec.probe, service=spec.service, window=settings.fault_probe_window
            )
            values = [
                point.v
                for series in result.series
                for point in series.points[-1:]
                if point.v is not None
            ]
            if not values:
                return ProbeReading(value=None, detail="no data in window")
            point_value = max(values)
            unit = {"error_rate": "ratio", "p95_latency": "ms", "throughput": "rps"}[spec.probe]
            return ProbeReading(
                value=point_value,
                detail=f"{spec.service} {spec.probe}={point_value:.4g} {unit}",
                meta={"series": len(result.series), "window": settings.fault_probe_window},
            )

        if spec.probe == "load_users":
            payload = await self._locust.get_json("/stats/requests")
            users = float(payload.get("user_count") or 0)
            return ProbeReading(
                value=users,
                detail=f"locust user_count={users:.0f}",
                meta={"state": payload.get("state")},
            )

        name = container or spec.service
        item = await _inspect(name)
        state = _state(item)
        if spec.probe == "container_memory":
            rows = await docker_json(
                "stats", "--no-stream", "--format", "{{json .}}", timeout_s=20.0
            )
            row = next((entry for entry in rows if entry.get("name") == name), None)
            if row is None:
                return ProbeReading(value=None, detail="no docker stats sample")
            used = str(row.get("MemUsage") or "").split("/")[0].strip()
            usage = _usage_mb(used)
            return ProbeReading(value=usage, detail=f"{name} memory {used}")
        if spec.probe == "container_running":
            return ProbeReading(
                value=1.0 if state["running"] else 0.0, detail=f"{name} state={state['status']}"
            )
        if spec.probe == "container_paused":
            return ProbeReading(
                value=1.0 if state["paused"] else 0.0, detail=f"{name} paused={state['paused']}"
            )
        if spec.probe == "container_health":
            return ProbeReading(
                value=1.0 if state["health"] == "unhealthy" else 0.0,
                detail=f"{name} health={state['health']}",
            )
        if spec.probe == "network_attached":
            attached = settings.demo_network in state["networks"]
            return ProbeReading(
                value=1.0 if attached else 0.0,
                detail=f"{name} networks={','.join(state['networks']) or 'none'}",
            )
        if spec.probe in ("cpu_limit", "memory_limit"):
            key = "cpu_limit_cores" if spec.probe == "cpu_limit" else "memory_limit_mb"
            value = state[key]
            return ProbeReading(
                value=float(value) if value is not None else 0.0,
                detail=f"{name} {key}={value}",
            )
        return ProbeReading(value=None, detail=f"unsupported probe {spec.probe}")


def _usage_mb(text: str) -> float | None:
    import re

    match = re.match(r"^\s*([\d.]+)\s*([A-Za-z]+)", text)
    if not match:
        return None
    amount, unit = float(match.group(1)), match.group(2).lower()
    factor = {"b": 1 / 1024 / 1024, "kib": 1 / 1024, "mib": 1.0, "gib": 1024.0}.get(unit)
    return round(amount * factor, 2) if factor is not None else None


def symptom_satisfied(spec: SymptomSpec, reading: ProbeReading, baseline: float | None) -> bool:
    value = reading.value
    if value is None:
        return False

    if spec.probe in ("cpu_limit", "memory_limit"):
        # The limit changing *below* its pre-image is the signal. A container
        # that had no limit before is constrained when a positive limit appears.
        if baseline is None or baseline == 0.0:
            return value > 0
        return value < baseline

    threshold = spec.threshold
    if spec.baseline_multiplier is not None:
        if baseline is None:
            return False
        threshold = baseline * spec.baseline_multiplier
        if spec.probe == "p95_latency":
            threshold = max(threshold, baseline + 50.0)

    if threshold is None:
        return False
    return _compare(value, spec.comparator, threshold)


class Mechanisms:
    """Every write the lab can perform, one method per mechanism."""

    def __init__(
        self,
        *,
        flagd: FlagdWriter | None = None,
        locust: AsyncHttpClient | None = None,
    ) -> None:
        self.flagd = flagd or FlagdWriter()
        self._locust = locust or shared_client(
            "load-generator", base_url=settings.load_generator_url, timeout_s=8.0
        )

    @property
    def locust(self) -> AsyncHttpClient:
        return self._locust

    # --- flagd -------------------------------------------------------------
    async def inject_flagd(self, flag: str, variant: str) -> MechanismOutcome:
        try:
            state = await self.flagd.set_variant(flag, variant)
        except FlagdWriteError as exc:
            raise ApiError(502, "flagd_write_failed", str(exc)) from exc
        return MechanismOutcome(
            ref={
                "mechanism": Mechanism.FLAGD.value,
                "flag": flag,
                "before": state.before,
                "after": variant,
                "file": state.file,
                "wrote_at": state.wrote_at.isoformat(),
                "read_back": state.read_back or {},
            },
            detail=f"{flag}: {state.before} -> {variant}; OFREP confirms {variant}",
        )

    async def revert_flagd(self, ref: dict[str, Any]) -> MechanismOutcome:
        flag = str(ref.get("flag") or "")
        before = str(ref.get("before") or "off")
        if not flag:
            raise ApiError(422, "invalid_revert", "missing flag in the injection pre-image")
        try:
            state = await self.flagd.set_variant(flag, before)
        except FlagdWriteError as exc:
            raise ApiError(502, "flagd_write_failed", str(exc)) from exc
        return MechanismOutcome(
            ref={
                "mechanism": Mechanism.FLAGD.value,
                "flag": flag,
                "restored": state.after,
                "read_back": state.read_back or {},
            },
            detail=f"{flag} restored to {before}; OFREP confirms {before}",
        )

    # --- container ---------------------------------------------------------
    async def inject_stop(self, target: ContainerTarget) -> MechanismOutcome:
        item = await _inspect(target.container)
        before = _state(item)
        await run_docker("stop", target.container, timeout_s=35.0)
        after = await wait_for_container(target.container, lambda s: not s["running"])
        return MechanismOutcome(
            ref={"mechanism": Mechanism.CONTAINER_STOP.value, "before": before, "after": after},
            detail=f"{target.container} stopped",
        )

    async def revert_stop(self, target: ContainerTarget, ref: dict[str, Any]) -> MechanismOutcome:
        await run_docker("start", target.container, timeout_s=60.0)
        after = await wait_for_container(target.container, lambda s: s["running"], timeout_s=60.0)
        return MechanismOutcome(
            ref={"mechanism": Mechanism.CONTAINER_STOP.value, "restored": after},
            detail=f"{target.container} started ({after['status']})",
        )

    async def inject_pause(self, target: ContainerTarget) -> MechanismOutcome:
        before = _state(await _inspect(target.container))
        await run_docker("pause", target.container, timeout_s=20.0)
        after = await wait_for_container(target.container, lambda s: s["paused"])
        return MechanismOutcome(
            ref={"mechanism": Mechanism.CONTAINER_PAUSE.value, "before": before, "after": after},
            detail=f"{target.container} paused",
        )

    async def revert_pause(self, target: ContainerTarget, ref: dict[str, Any]) -> MechanismOutcome:
        await run_docker("unpause", target.container, timeout_s=20.0)
        after = await wait_for_container(target.container, lambda s: not s["paused"])
        return MechanismOutcome(
            ref={"mechanism": Mechanism.CONTAINER_PAUSE.value, "restored": after},
            detail=f"{target.container} unpaused",
        )

    # --- network -----------------------------------------------------------
    async def inject_partition(self, target: ContainerTarget) -> MechanismOutcome:
        if not target.networks:
            raise ApiError(409, "no_network", f"{target.container} has no network to detach")
        before = _state(await _inspect(target.container))
        await run_docker("network", "disconnect", target.network, target.container, timeout_s=30.0)
        after = await wait_for_container(
            target.container, lambda s: settings.demo_network not in s["networks"]
        )
        return MechanismOutcome(
            ref={
                "mechanism": Mechanism.NETWORK_PARTITION.value,
                "network": target.network,
                "aliases": target.network_aliases,
                "before": before,
                "after": after,
            },
            detail=f"{target.container} disconnected from {target.network}",
        )

    async def revert_partition(
        self, target: ContainerTarget, ref: dict[str, Any]
    ) -> MechanismOutcome:
        network = str(ref.get("network") or target.network)
        aliases = [str(alias) for alias in (ref.get("aliases") or [])]
        args = ["network", "connect"]
        for alias in aliases:
            args.extend(["--alias", alias])
        args.extend([network, target.container])
        await run_docker(*args, timeout_s=30.0)
        after = await wait_for_container(
            target.container, lambda s: settings.demo_network in s["networks"]
        )
        return MechanismOutcome(
            ref={"mechanism": Mechanism.NETWORK_PARTITION.value, "restored": after},
            detail=f"{target.container} reconnected to {network} ({len(aliases)} aliases)",
        )

    # --- limits ------------------------------------------------------------
    async def inject_limits(
        self, target: ContainerTarget, params: dict[str, Any]
    ) -> MechanismOutcome:
        before = _state(await _inspect(target.container))
        args = ["update"]
        applied: dict[str, Any] = {}
        if "cpus" in params:
            cpus = float(params["cpus"])
            args.extend(["--cpus", f"{cpus:g}"])
            applied["cpus"] = cpus
        if "memory_mb" in params:
            memory = int(params["memory_mb"])
            args.extend(["--memory", f"{memory}m", "--memory-swap", f"{memory * 2}m"])
            applied["memory_mb"] = memory
        if not applied:
            raise ApiError(422, "invalid_param", "cpu_pressure/memory_pressure need a limit")
        args.append(target.container)
        await run_docker(*args, timeout_s=25.0)

        def limited(state: dict[str, Any]) -> bool:
            if "cpus" in applied:
                current = state["cpu_limit_cores"]
                if current is None or current > applied["cpus"] + 0.001:
                    return False
            if "memory_mb" in applied:
                current_mb = state["memory_limit_mb"]
                if current_mb is None or current_mb > applied["memory_mb"]:
                    return False
            return True

        after = await wait_for_container(target.container, limited)
        return MechanismOutcome(
            ref={
                "mechanism": Mechanism.RESOURCE_LIMIT.value,
                "applied": applied,
                "before": before,
                "after": after,
            },
            detail=f"{target.container} limited to {applied}",
        )

    async def revert_limits(self, target: ContainerTarget, ref: dict[str, Any]) -> MechanismOutcome:
        before = ref.get("before") or {}
        applied = ref.get("applied") or {}
        args = ["update"]
        cpu_reset: str | None = None
        if "cpus" in applied:
            cpus = before.get("cpu_limit_cores")
            if cpus:
                args.extend(["--cpus", f"{float(cpus):g}"])
            else:
                # `docker update --cpus 0` is a no-op; NCPU is the effective
                # unlimited value for a single container.
                cores = await host_cores()
                args.extend(["--cpus", str(cores)])
                cpu_reset = f"host-cores ({cores})"
        if "memory_mb" in applied:
            memory = before.get("memory_limit_mb")
            args.extend(["--memory", f"{int(memory)}m" if memory else "0"])
            if memory:
                swap = before.get("memory_swap_mb") or int(memory) * 2
                args.extend(["--memory-swap", f"{int(swap)}m"])
            else:
                args.extend(["--memory-swap", "-1"])
        args.append(target.container)
        await run_docker(*args, timeout_s=25.0)

        expected_cpu = before.get("cpu_limit_cores") or (await host_cores())

        def restored(state: dict[str, Any]) -> bool:
            if "cpus" in applied:
                expected = expected_cpu
                if expected is None:
                    if state["cpu_limit_cores"] is not None:
                        return False
                elif state["cpu_limit_cores"] != expected:
                    return False
            if "memory_mb" in applied:
                expected_mb = before.get("memory_limit_mb")
                if expected_mb is not None and state["memory_limit_mb"] != expected_mb:
                    return False
            return True

        after = await wait_for_container(target.container, restored)
        detail = f"{target.container} limits restored"
        if cpu_reset:
            detail += f"; CPU limit reset via {cpu_reset} (Docker cannot clear it)"
        return MechanismOutcome(
            ref={
                "mechanism": Mechanism.RESOURCE_LIMIT.value,
                "restored": after,
                "cpu_reset": cpu_reset,
            },
            detail=detail,
        )

    # --- load --------------------------------------------------------------
    async def inject_load(
        self, target: ContainerTarget, params: dict[str, Any]
    ) -> MechanismOutcome:
        users = int(params["users"])
        payload = await self._locust.get_json("/stats/requests")
        before = int(payload.get("user_count") or 0)
        await self._locust.post_form(
            "/swarm",
            data={
                "user_count": str(users),
                "spawn_rate": str(max(5, users // 4)),
                "host": "",
            },
        )
        after = await self._wait_users(users)
        return MechanismOutcome(
            ref={
                "mechanism": Mechanism.LOAD_SPIKE.value,
                "before": {"users": before},
                "after": {"users": after},
            },
            detail=f"locust users {before} -> {after}",
        )

    async def revert_load(self, target: ContainerTarget, ref: dict[str, Any]) -> MechanismOutcome:
        before = int((ref.get("before") or {}).get("users") or 5)
        await self._locust.post_form(
            "/swarm",
            data={
                "user_count": str(before),
                "spawn_rate": str(max(1, before // 2)),
                "host": "",
            },
        )
        after = await self._wait_users(before)
        return MechanismOutcome(
            ref={"mechanism": Mechanism.LOAD_SPIKE.value, "restored": {"users": after}},
            detail=f"locust users restored to {after}",
        )

    async def _wait_users(self, expected: int, *, timeout_s: float = 25.0) -> int:
        deadline = time.monotonic() + timeout_s
        last = -1
        while time.monotonic() < deadline:
            payload = await self._locust.get_json("/stats/requests")
            last = int(payload.get("user_count") or 0)
            if last == expected:
                return last
            await asyncio.sleep(0.5)
        raise ApiError(
            502,
            "load_not_applied",
            f"locust did not reach {expected} users (last {last})",
        )


__all__ = [
    "FaultProbe",
    "MechanismOutcome",
    "Mechanisms",
    "ProbeReading",
    "symptom_satisfied",
    "wait_for_container",
]
