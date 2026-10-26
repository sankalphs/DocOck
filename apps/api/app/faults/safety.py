"""The fault-controller boundary.

A target is writable only when **all** of these hold:

1. Its name is in the catalog allowlist (checked without touching Docker).
2. A container with `com.docker.compose.project=<demo project>` exists for it.
3. `com.docker.compose.service` equals the requested service name.
4. It is attached to exactly one network, and that network is the demo network.

Any failure is an `ApiError(403, "unsafe_target")` raised **before** the first
mutation. The label — not a network name or a naming convention — is the
boundary, because Compose sets it on every container it owns.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.config import settings
from app.errors import ApiError
from app.faults.catalog import ALLOWED_TARGETS
from app.telemetry.docker import DockerError, docker_inspect, docker_json

PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"


class ContainerTarget(BaseModel):
    service: str
    container: str
    project: str
    image: str
    state: str
    paused: bool
    health: str
    networks: list[str] = Field(default_factory=list)
    network_aliases: list[str] = Field(default_factory=list)
    cpu_limit_cores: float | None = None
    memory_limit_mb: int | None = None
    memory_swap_mb: int | None = None
    labels: dict[str, str] = Field(default_factory=dict)

    @property
    def network(self) -> str:
        return self.networks[0]


def _unsafe(service: str, reason: str, detail: Any | None = None) -> ApiError:
    return ApiError(
        403,
        "unsafe_target",
        f"Refusing to touch '{service}': {reason}",
        {"target": service, "reason": reason, "detail": detail},
    )


async def resolve_target(
    service: str, *, project: str | None = None, require_network: bool = True
) -> ContainerTarget:
    """Resolve a service to its container, or raise 403. Read-only.

    `require_network=False` is only used to revert an existing partition, where
    the missing attachment is the expected state. The label check still runs.
    """
    if service not in ALLOWED_TARGETS:
        raise _unsafe(
            service,
            "not in the demo service allowlist",
            {"allowed_targets": sorted(ALLOWED_TARGETS)},
        )

    project = project or settings.demo_compose_project
    try:
        rows = await docker_json(
            "ps",
            "-a",
            "--filter",
            f"label={PROJECT_LABEL}={project}",
            "--format",
            "{{json .}}",
            timeout_s=10.0,
        )
    except DockerError as exc:
        raise ApiError(502, "docker_error", f"Docker is unavailable: {exc}") from exc

    candidates = [
        row
        for row in rows
        if str(row.get("Names") or "") == service
        or (row.get("Labels") or "").split(",").count(f"{SERVICE_LABEL}={service}") > 0
    ]
    if not candidates:
        raise _unsafe(service, f"no container labelled {PROJECT_LABEL}={project}")
    if len(candidates) > 1:
        raise ApiError(
            409,
            "ambiguous_target",
            f"'{service}' matches {len(candidates)} containers",
            {"containers": [row.get("Names") for row in candidates]},
        )

    name = str(candidates[0].get("Names") or service)
    try:
        items = await docker_inspect(name, timeout_s=10.0)
    except DockerError as exc:
        raise ApiError(502, "docker_error", f"docker inspect failed: {exc}") from exc
    if not items:
        raise _unsafe(service, "container disappeared during inspection")
    item = items[0]

    labels = {
        str(key): str(value)
        for key, value in ((item.get("Config") or {}).get("Labels") or {}).items()
    }
    if labels.get(PROJECT_LABEL) != project:
        raise _unsafe(
            service, f"missing {PROJECT_LABEL}={project} label", labels.get(PROJECT_LABEL)
        )
    if labels.get(SERVICE_LABEL) != service:
        raise _unsafe(
            service,
            "compose service label does not match the requested service",
            labels.get(SERVICE_LABEL),
        )

    networks = sorted((((item.get("NetworkSettings") or {}).get("Networks")) or {}).keys())
    attachments = ((item.get("NetworkSettings") or {}).get("Networks")) or {}
    aliases: list[str] = []
    if networks:
        attachment = attachments.get(networks[0]) or {}
        for alias in (attachment.get("Aliases") or []) + (attachment.get("DNSNames") or []):
            if alias and alias not in aliases:
                aliases.append(str(alias))
    if not networks:
        if require_network:
            raise _unsafe(service, "container is not attached to a network")
    else:
        if len(networks) > 1:
            raise _unsafe(service, "container is attached to more than one network", networks)
        if not settings.demo_network:
            raise _unsafe(service, "demo network is not configured")
        if networks[0] != settings.demo_network:
            raise _unsafe(service, "container is not on the demo network", networks[0])

    state = item.get("State") or {}
    host = item.get("HostConfig") or {}
    nano_cpus = int(host.get("NanoCpus") or 0)
    memory_bytes = int(host.get("Memory") or 0)
    swap_bytes = int(host.get("MemorySwap") or 0)

    return ContainerTarget(
        service=service,
        container=name,
        project=project,
        image=str((item.get("Config") or {}).get("Image") or ""),
        state=str(state.get("Status") or "unknown"),
        paused=bool(state.get("Paused")),
        health=str((state.get("Health") or {}).get("Status") or "none"),
        networks=networks,
        network_aliases=aliases,
        cpu_limit_cores=round(nano_cpus / 1e9, 3) if nano_cpus else None,
        memory_limit_mb=round(memory_bytes / 1024 / 1024) if memory_bytes else None,
        memory_swap_mb=round(swap_bytes / 1024 / 1024) if swap_bytes else None,
        labels=labels,
    )


async def snapshots(service: str) -> dict[str, Any]:
    """A read-only pre-image an operator can inspect in an audit row."""
    target = await resolve_target(service)
    return {
        "container": target.container,
        "state": target.state,
        "paused": target.paused,
        "health": target.health,
        "network": target.network,
        "cpu_limit_cores": target.cpu_limit_cores,
        "memory_limit_mb": target.memory_limit_mb,
    }


__all__ = ["PROJECT_LABEL", "SERVICE_LABEL", "ContainerTarget", "resolve_target", "snapshots"]
