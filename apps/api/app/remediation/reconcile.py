"""Reconciler: bring one drifted component back to its desired state.

The executor calls this after it has reverted any *recorded* fault the action
covers, so whatever drift is left was caused by something DocOck did not
inject. Every write goes through the same safety boundary as a fault
(`resolve_target`: allowlist, compose labels, demo network), and every step is
read back before it counts as done.

Which drift an action may fix is declared here (`ACTION_SCOPE`), so an action
never strays outside its catalog description: `restore_network` reconnects,
it does not restart; only `reconcile_service` / `reconcile_all` do everything.
"""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.errors import ApiError
from app.faults.mechanisms import host_cores, wait_for_container
from app.faults.safety import resolve_target
from app.logging import get_logger
from app.remediation.drift import DriftItem, DriftReport, DriftScanner, scanner
from app.telemetry.docker import run_docker

log = get_logger(__name__)

ALL_KINDS = frozenset(
    {
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
    }
)

# action -> drift kinds it is allowed to fix.
ACTION_SCOPE: dict[str, frozenset[str]] = {
    "restart_service": frozenset(
        {
            "container_stopped",
            "container_crashed",
            "container_restarting",
            "container_paused",
            "container_unhealthy",
        }
    ),
    "restore_network": frozenset({"network_detached"}),
    "remove_resource_limit": frozenset({"cpu_limited", "memory_limited"}),
    "clear_feature_flag": frozenset({"flag_on"}),
    "stop_load_generator": frozenset({"load_changed", "flag_on"}),
    "reset_fault": ALL_KINDS,
    "reconcile_service": ALL_KINDS,
    "reconcile_all": ALL_KINDS,
}


def relevant(action_id: str, target: str, report: DriftReport) -> list[DriftItem]:
    """The drift items `action_id` on `target` would fix, in fix order."""
    scope = ACTION_SCOPE.get(action_id, frozenset())
    out: list[DriftItem] = []
    for item in report.items:
        if item.kind not in scope or item.fix is None:
            continue
        if action_id == "reconcile_all":
            out.append(item)
        elif action_id == "stop_load_generator":
            if item.fix.action_id == "stop_load_generator":
                out.append(item)
        elif item.service == target or item.fix.target == target:
            if action_id == "clear_feature_flag" and item.fix.action_id != "clear_feature_flag":
                continue
            out.append(item)
    return out


class Reconciler:
    def __init__(self, drift: DriftScanner | None = None) -> None:
        self.scanner = drift or scanner

    async def fix(self, item: DriftItem) -> dict[str, Any]:
        """Apply one fix and read it back. Raises ApiError on failure."""
        kind = item.kind
        if kind == "flag_on":
            return await self._flag(item)
        if kind == "load_changed":
            return await self._load(item)
        if kind in ("cpu_limited", "memory_limited"):
            return await self._limits(item)
        if kind == "network_detached":
            return await self._network(item)
        if kind in (
            "container_stopped",
            "container_crashed",
            "container_restarting",
            "container_paused",
            "container_unhealthy",
        ):
            return await self._container(item)
        raise ApiError(422, "not_reconcilable", f"{kind} on {item.service} has no automatic fix")

    async def apply(self, action_id: str, target: str) -> list[dict[str, Any]]:
        """Fix every drift item in scope; re-scan between fixes (one can fix another)."""
        steps: list[dict[str, Any]] = []
        done: set[tuple[str, str]] = set()
        services = None if action_id in ("reconcile_all", "stop_load_generator") else [target]
        for _round in range(6):
            report = await self.scanner.scan(services=services)
            pending = [
                item for item in relevant(action_id, target, report) if item.key() not in done
            ]
            if not pending:
                break
            item = pending[0]
            done.add(item.key())
            outcome = await self.fix(item)
            steps.append({"drift": item.kind, "component": item.component, **outcome})
            log.info("drift_fixed", kind=item.kind, component=item.component, action=action_id)
        return steps

    # --- fixes ----------------------------------------------------------------
    async def _flag(self, item: DriftItem) -> dict[str, Any]:
        from app.faults.controller import get_controller

        key = item.component.removeprefix("flag:")
        wanted = str(item.desired or "off")
        state = await get_controller().mechanisms.flagd.set_variant(key, wanted)
        return {"flag": key, "before": state.before, "after": wanted, "read_back": True}

    async def _load(self, item: DriftItem) -> dict[str, Any]:
        from app.faults.controller import get_controller

        users = int(item.desired or self.scanner.store.desired_users())
        locust = get_controller().mechanisms.locust
        await locust.post_form(
            "/swarm",
            data={"user_count": str(users), "spawn_rate": str(max(1, users // 2)), "host": ""},
        )
        reached = await get_controller().mechanisms._wait_users(users)
        return {"load_users": reached, "before": item.observed}

    async def _limits(self, item: DriftItem) -> dict[str, Any]:
        target = await resolve_target(item.service, require_network=False)
        desired = self.scanner.store.load().containers.get(item.service)
        args = ["update"]
        if item.kind == "cpu_limited":
            cpus = desired.cpu_limit_cores if desired else None
            args += ["--cpus", f"{float(cpus):g}" if cpus else str(await host_cores())]
        else:
            memory = desired.memory_limit_mb if desired else None
            if memory:
                swap = (desired.memory_swap_mb if desired else None) or memory * 2
                swap = int(max(swap, memory))
                args += ["--memory", f"{int(memory)}m", "--memory-swap", f"{swap}m"]
            else:
                args += ["--memory", "0", "--memory-swap", "-1"]
        args.append(target.container)
        await run_docker(*args, timeout_s=25.0)
        state = await wait_for_container(target.container, lambda s: True)
        restarted = False
        if not state["running"] or state.get("restarting"):
            await run_docker("restart", target.container, timeout_s=60.0)
            state = await wait_for_container(
                target.container,
                lambda s: s["running"] and not s.get("restarting"),
                timeout_s=60.0,
            )
            restarted = True
        return {
            "docker": " ".join(args[:-1]),
            "container": target.container,
            "restarted": restarted,
            "cpu_limit_cores": state.get("cpu_limit_cores"),
            "memory_limit_mb": state.get("memory_limit_mb"),
        }

    async def _network(self, item: DriftItem) -> dict[str, Any]:
        target = await resolve_target(item.service, require_network=False)
        desired = self.scanner.store.load().containers.get(item.service)
        network = (desired.network if desired else None) or settings.demo_network
        aliases = list(desired.aliases) if desired and desired.aliases else [item.service]
        if item.service not in aliases:
            aliases.append(item.service)
        args = ["network", "connect"]
        for alias in aliases:
            args += ["--alias", alias]
        args += [network, target.container]
        await run_docker(*args, timeout_s=30.0)
        state = await wait_for_container(target.container, lambda s: network in s["networks"])
        return {"network": network, "aliases": aliases, "networks": state["networks"]}

    async def _container(self, item: DriftItem) -> dict[str, Any]:
        target = await resolve_target(item.service, require_network=False)
        if item.kind == "container_paused" or target.paused:
            verb = "unpause"
        elif item.kind == "container_unhealthy" or (
            item.kind == "container_restarting" and target.state == "restarting"
        ):
            verb = "restart"
        else:
            verb = "start"
        await run_docker(verb, target.container, timeout_s=60.0)
        state = await wait_for_container(
            target.container,
            lambda s: s["running"] and not s["paused"] and not s.get("restarting"),
            timeout_s=60.0,
        )
        return {"docker": verb, "container": target.container, "state": state["status"]}


reconciler = Reconciler()

__all__ = ["ACTION_SCOPE", "Reconciler", "reconciler", "relevant"]
