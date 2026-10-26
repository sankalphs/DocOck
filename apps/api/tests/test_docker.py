from __future__ import annotations

from typing import Any

import pytest

from app.telemetry import docker as docker_module
from app.telemetry.docker import DockerAdapter, parse_container, parse_stats
from tests.conftest import load_fixture


def test_parse_container_normalises_inspect_output() -> None:
    item = load_fixture("docker_inspect.json")[0]
    info = parse_container(item)

    assert info.name == "payment"
    assert info.service == "payment"
    assert info.state == "running"
    assert info.health == "healthy"
    assert info.paused is False
    assert info.oom_killed is False
    assert info.memory_limit_mb == 140
    assert info.cpu_limit_cores is None
    assert "opentelemetry-demo" in info.networks
    assert info.started_at is not None and info.started_at.tzinfo is not None


def test_parse_container_reads_paused_and_oom_flags() -> None:
    item = load_fixture("docker_inspect.json")[0]
    item["State"]["Paused"] = True
    item["State"]["OOMKilled"] = True
    item["RestartCount"] = 3

    info = parse_container(item)

    assert info.paused is True
    assert info.oom_killed is True
    assert info.restart_count == 3
    assert "paused" in info.status_text


def test_parse_stats_reads_percentages_and_units() -> None:
    row = load_fixture("docker_stats.json")[0]
    parsed = parse_stats(row)

    assert parsed["name"] == "payment"
    # Values are sampled, not fixed: assert the shape, not the reading.
    assert parsed["cpu_percent"] is not None and parsed["cpu_percent"] >= 0
    assert parsed["memory_used_mb"] is not None and 0 < parsed["memory_used_mb"] <= 140
    assert parsed["memory_percent"] is not None and 0 <= parsed["memory_percent"] <= 100


async def test_containers_join_ps_inspect_and_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    async def fake_docker_json(*args: str, timeout_s: float = 15.0) -> Any:
        calls.append(args)
        command = args[0]
        if command == "ps":
            return load_fixture("docker_ps.json")
        if command == "stats":
            return load_fixture("docker_stats.json")
        return []

    async def fake_docker_inspect(*names: str, timeout_s: float = 20.0) -> Any:
        calls.append(("inspect", *names))
        return load_fixture("docker_inspect.json")

    monkeypatch.setattr(docker_module, "docker_json", fake_docker_json)
    monkeypatch.setattr(docker_module, "docker_inspect", fake_docker_inspect)
    adapter = DockerAdapter(project="docock-demo", ttl_s=5)

    containers = await adapter.containers()

    assert [container.name for container in containers] == ["payment"]
    payment = containers[0]
    assert payment.cpu_percent is not None and payment.cpu_percent >= 0
    assert payment.memory_used_mb is not None and payment.memory_used_mb > 0
    # ps + inspect + stats, in that order, all scoped to the Compose project.
    assert calls[0][0] == "ps"
    assert any("com.docker.compose.project=docock-demo" in arg for arg in calls[0])
    assert calls[1][0] == "inspect"

    # Second call is served from cache.
    await adapter.containers()
    assert len([call for call in calls if call[0] == "inspect"]) == 1


async def test_containers_without_stats_skip_docker_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_docker_json(*args: str, timeout_s: float = 15.0) -> Any:
        if args[0] == "ps":
            return load_fixture("docker_ps.json")
        raise AssertionError("docker stats should not be called")

    async def fake_docker_inspect(*names: str, timeout_s: float = 20.0) -> Any:
        return load_fixture("docker_inspect.json")

    monkeypatch.setattr(docker_module, "docker_json", fake_docker_json)
    monkeypatch.setattr(docker_module, "docker_inspect", fake_docker_inspect)
    adapter = DockerAdapter(ttl_s=5)

    containers = await adapter.containers(include_stats=False)

    assert containers[0].cpu_percent is None


async def test_events_are_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_docker_json(*args: str, timeout_s: float = 15.0) -> Any:
        return [
            {
                "Action": "die",
                "Actor": {
                    "ID": "abc",
                    "Attributes": {
                        "name": "payment",
                        "com.docker.compose.service": "payment",
                        "exitCode": "137",
                    },
                },
                "time": 1792820927,
            }
        ]

    monkeypatch.setattr(docker_module, "docker_json", fake_docker_json)
    adapter = DockerAdapter(ttl_s=0)

    events = await adapter.events(lookback_s=60)

    assert len(events) == 1
    assert events[0].action == "die"
    assert events[0].container == "payment"
    assert events[0].service == "payment"
    assert events[0].attributes["exitCode"] == "137"
