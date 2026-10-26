"""Container and fault signals for an incident window.

PLAN §10 lists "container state" and "flag state" as evidence. Both are direct
observations: Docker's state/health/limits and the fault rows DocOck itself
wrote. They never replace the metric/trace evidence — they explain it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.db.models import Fault
from app.telemetry.docker import DockerAdapter


class ContainerSignal(BaseModel):
    service: str
    container: str
    state: str
    health: str
    paused: bool
    restarts: int
    oom_killed: bool
    cpu_limit_cores: float | None = None
    memory_limit_mb: int | None = None
    memory_mb: float | None = None
    cpu_percent: float | None = None
    # None = not inspected; [] = running with every network detached.
    networks: list[str] | None = None
    detail: str = ""

    @property
    def detached(self) -> bool:
        return self.state == "running" and self.networks is not None and not self.networks

    @property
    def unhealthy(self) -> bool:
        # Restarts/OOMKilled are historical flags on the container (a restart
        # hours ago sets RestartCount forever), so they are reported in the
        # evidence but do not count as a current symptom.
        return self.state != "running" or self.paused or self.health == "unhealthy" or self.detached


class FaultSignal(BaseModel):
    id: int
    fault_id: str
    target: str
    mechanism: str
    params: dict = Field(default_factory=dict)
    status: str
    injected_at: datetime
    reset_at: datetime | None = None
    detail: str = ""


async def container_signals(
    docker: DockerAdapter,
    services: list[str],
    containers: list[Any] | None = None,
) -> dict[str, ContainerSignal]:
    try:
        items = (
            containers if containers is not None else await docker.containers(include_stats=True)
        )
    except Exception:
        return {}
    wanted = set(services)
    out: dict[str, ContainerSignal] = {}
    for item in items:
        if item.service not in wanted:
            continue
        out[item.service] = ContainerSignal(
            service=item.service,
            container=item.name,
            state=item.state,
            health=item.health,
            paused=item.paused,
            restarts=item.restart_count,
            oom_killed=item.oom_killed,
            cpu_limit_cores=item.cpu_limit_cores,
            memory_limit_mb=item.memory_limit_mb,
            memory_mb=item.memory_used_mb,
            cpu_percent=item.cpu_percent,
            networks=list(item.networks),
            detail=(
                f"{item.name} {item.status_text}; "
                f"networks={','.join(item.networks) or 'none'}; restarts={item.restart_count}; "
                f"memory={item.memory_used_mb or 0:.0f}/"
                f"{item.memory_limit_mb or 0} MB; cpu_limit={item.cpu_limit_cores or 'none'}"
            ),
        )
    return out


def fault_signals(faults: list[Fault]) -> list[FaultSignal]:
    from app.faults.catalog import DEFINITIONS

    out: list[FaultSignal] = []
    for fault in faults:
        definition = DEFINITIONS.get(fault.type)
        mechanism = definition.mechanism.value if definition else "unknown"
        detail = f"{fault.type} on {fault.target} {fault.status}"
        if fault.params:
            detail += f" {fault.params}"
        out.append(
            FaultSignal(
                id=fault.id,
                fault_id=fault.type,
                target=fault.target,
                mechanism=mechanism,
                params=dict(fault.params or {}),
                status=fault.status,
                injected_at=fault.injected_at,
                reset_at=fault.reset_at,
                detail=detail,
            )
        )
    return out


__all__ = ["ContainerSignal", "FaultSignal", "container_signals", "fault_signals"]
