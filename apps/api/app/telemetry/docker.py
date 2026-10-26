"""Docker CLI access.

We shell out to the Docker CLI instead of binding the socket: the API runs on the
host, the CLI is already the supported entry point on Windows, and it keeps the
process free of a Docker SDK dependency. Every call is bounded by a timeout so a
wedged daemon cannot block the event loop.

Safety: every read here is filtered by `com.docker.compose.project=docock-demo`.
The same label is the boundary the fault controller will enforce in P6 — a
container outside that project is never a target, even if it is running.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import UTC, datetime
from typing import Any

from app.config import settings
from app.telemetry.cache import TTLCache
from app.telemetry.dto import ContainerInfo, DockerEvent


class DockerError(RuntimeError):
    pass


async def run_docker(*args: str, timeout_s: float = 10.0) -> str:
    """Run a docker command and return stdout. Raises DockerError on failure."""
    process = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise DockerError(f"docker {args[0]} timed out after {timeout_s}s") from exc

    if process.returncode != 0:
        message = stderr.decode("utf-8", "replace").strip() or "no stderr"
        raise DockerError(f"docker {args[0]} exited {process.returncode}: {message}")
    return stdout.decode("utf-8", "replace")


async def docker_json(*args: str, timeout_s: float = 15.0) -> Any:
    """Run a `--format {{json .}}` command and parse each line as JSON."""
    output = (await run_docker(*args, timeout_s=timeout_s)).strip()
    if not output:
        return []
    return [json.loads(line) for line in output.splitlines() if line.strip()]


async def docker_inspect(*names: str, timeout_s: float = 20.0) -> list[dict[str, Any]]:
    """`docker inspect` emits one JSON array, not one object per line."""
    output = await run_docker("inspect", *names, timeout_s=timeout_s)
    data = json.loads(output)
    return data if isinstance(data, list) else [data]


async def server_version() -> str:
    return (await run_docker("version", "--format", "{{.Server.Version}}", timeout_s=8.0)).strip()


def _parse_time(value: str | int | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        # `docker events --format {{json .}}` emits epoch seconds.
        return datetime.fromtimestamp(float(value), UTC)
    if not value or value.startswith("0001-01-01"):
        return None
    text = value.replace("Z", "+00:00")
    match = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d{1,9})(.*)$", text)
    if match:
        text = f"{match.group(1)}.{(match.group(2) + '000000')[:6]}{match.group(3)}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


_UNIT_TO_MB = {
    "b": 1 / 1024 / 1024,
    "kib": 1 / 1024,
    "mib": 1.0,
    "gib": 1024.0,
    "tib": 1024.0 * 1024,
}


def _parse_usage(value: str) -> float | None:
    match = re.match(r"^\s*([\d.]+)\s*([A-Za-z]+)\s*$", value or "")
    if not match:
        return None
    amount, unit = float(match.group(1)), match.group(2).lower()
    factor = _UNIT_TO_MB.get(unit)
    return round(amount * factor, 2) if factor is not None else None


def _parse_percent(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return round(float(value.strip().rstrip("%")), 2)
    except ValueError:
        return None


def parse_container(item: dict[str, Any]) -> ContainerInfo:
    state = item.get("State") or {}
    host = item.get("HostConfig") or {}
    labels = (item.get("Config") or {}).get("Labels") or {}
    name = str(item.get("Name") or "").lstrip("/")
    service = labels.get("com.docker.compose.service") or name
    health = (state.get("Health") or {}).get("Status") or "none"
    nano_cpus = int(host.get("NanoCpus") or 0)
    memory_bytes = int(host.get("Memory") or 0)
    networks = sorted(((item.get("NetworkSettings") or {}).get("Networks") or {}).keys())
    return ContainerInfo(
        name=name,
        service=service,
        image=str((item.get("Config") or {}).get("Image") or ""),
        state=str(state.get("Status") or "unknown"),
        health=str(health),
        paused=bool(state.get("Paused")),
        oom_killed=bool(state.get("OOMKilled")),
        restart_count=int(item.get("RestartCount") or 0),
        started_at=_parse_time(state.get("StartedAt")),
        cpu_limit_cores=round(nano_cpus / 1e9, 3) if nano_cpus else None,
        memory_limit_mb=round(memory_bytes / 1024 / 1024) if memory_bytes else None,
        networks=networks,
        status_text=_status_text(state),
    )


def _status_text(state: dict[str, Any]) -> str:
    status = str(state.get("Status") or "unknown")
    if state.get("Paused"):
        return f"{status} (paused)"
    if state.get("Restarting"):
        return f"{status} (restarting)"
    health = (state.get("Health") or {}).get("Status")
    return f"{status} ({health})" if health and health != "none" else status


def parse_stats(item: dict[str, Any]) -> dict[str, Any]:
    name = str(item.get("Name") or "")
    used = str(item.get("MemUsage") or "").split("/")[0]
    return {
        "name": name,
        "cpu_percent": _parse_percent(item.get("CPUPerc")),
        "memory_used_mb": _parse_usage(used),
        "memory_percent": _parse_percent(item.get("MemPerc")),
    }


class DockerAdapter:
    def __init__(self, project: str | None = None, ttl_s: float | None = None) -> None:
        self.project = project or settings.demo_compose_project
        self._cache = TTLCache(ttl_s if ttl_s is not None else settings.telemetry_cache_ttl_s)

    async def containers(self, *, include_stats: bool = True) -> list[ContainerInfo]:
        async def load() -> list[ContainerInfo]:
            listed = await docker_json(
                "ps",
                "-a",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
                "--format",
                "{{json .}}",
                timeout_s=10.0,
            )
            names = [item["Names"] for item in listed if item.get("Names")]
            if not names:
                return []
            inspected = await docker_inspect(*names, timeout_s=20.0)
            infos = [parse_container(item) for item in inspected]
            if include_stats:
                stats = await self._stats_by_name()
                for info in infos:
                    sample = stats.get(info.name)
                    if sample:
                        info.cpu_percent = sample["cpu_percent"]
                        info.memory_used_mb = sample["memory_used_mb"]
                        info.memory_percent = sample["memory_percent"]
            infos.sort(key=lambda info: info.service)
            return infos

        return await self._cache.get(("containers", include_stats), load)

    async def container(self, name: str) -> ContainerInfo:
        async def load() -> ContainerInfo:
            items = await docker_inspect(name, timeout_s=10.0)
            if not items:
                raise DockerError(f"container '{name}' not found")
            info = parse_container(items[0])
            stats = await self._stats_by_name()
            sample = stats.get(info.name)
            if sample:
                info.cpu_percent = sample["cpu_percent"]
                info.memory_used_mb = sample["memory_used_mb"]
                info.memory_percent = sample["memory_percent"]
            return info

        return await self._cache.get(("container", name), load)

    async def _stats_by_name(self) -> dict[str, dict[str, Any]]:
        async def load() -> dict[str, dict[str, Any]]:
            rows = await docker_json(
                "stats", "--no-stream", "--format", "{{json .}}", timeout_s=20.0
            )
            return {row["name"]: row for row in (parse_stats(item) for item in rows)}

        return await self._cache.get(("stats",), load)

    async def events(self, *, lookback_s: int = 300, limit: int = 200) -> list[DockerEvent]:
        lookback = max(10, min(3600, lookback_s))

        async def load() -> list[DockerEvent]:
            now = int(time.time())
            rows = await docker_json(
                "events",
                "--since",
                str(now - lookback),
                "--until",
                str(now),
                "--filter",
                f"label=com.docker.compose.project={self.project}",
                "--format",
                "{{json .}}",
                timeout_s=20.0,
            )
            events: list[DockerEvent] = []
            for row in rows:
                actor = row.get("Actor") or {}
                attributes = {
                    key: str(value) for key, value in (actor.get("Attributes") or {}).items()
                }
                events.append(
                    DockerEvent(
                        at=_parse_time(actor.get("Attributes", {}).get("time"))
                        or _parse_time(row.get("time"))
                        or datetime.now(UTC),
                        action=str(row.get("Action") or "unknown"),
                        container=str(attributes.get("name") or actor.get("ID") or "")[:64],
                        service=attributes.get("com.docker.compose.service"),
                        attributes={
                            key: value[:200]
                            for key, value in attributes.items()
                            if key
                            in ("name", "com.docker.compose.service", "image", "signal", "exitCode")
                        },
                    )
                )
            return events[-limit:]

        return await self._cache.get(("events", lookback, limit), load)


__all__ = [
    "ContainerInfo",
    "DockerAdapter",
    "DockerError",
    "DockerEvent",
    "docker_inspect",
    "docker_json",
    "parse_container",
    "parse_stats",
    "run_docker",
    "server_version",
]
