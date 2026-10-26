from __future__ import annotations

import json as jsonlib
import os
import time
from pathlib import Path
from typing import Any

import pytest

from app.errors import ApiError
from app.faults import safety
from app.faults.audit import AuditEntry, AuditLog
from app.faults.catalog import (
    ALLOWED_TARGETS,
    DEFINITIONS,
    FaultCategory,
    FaultRequest,
    Mechanism,
    catalog,
    resolve_request,
)
from app.faults.flagd import FlagdWriter
from app.faults.mechanisms import ProbeReading, SymptomSpec, symptom_satisfied


def _flag_file(tmp_path: Path, *, mtime: float | None = None) -> Path:
    path = tmp_path / "demo.flagd.json"
    path.write_text(
        jsonlib.dumps(
            {
                "flags": {
                    "paymentFailure": {
                        "defaultVariant": "off",
                        "variants": {"off": 0, "10%": 0.1, "100%": 1},
                    },
                    "adFailure": {
                        "defaultVariant": "on",
                        "variants": {"off": False, "on": True},
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class FakeOFREP:
    """Reads the flag file on every evaluation, like flagd does."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls = 0

    async def post_json(self, url: str, *, json: Any = None, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        key = url.rsplit("/", 1)[-1]
        data = jsonlib.loads(self.path.read_text(encoding="utf-8"))
        flag = data["flags"][key]
        return {
            "key": key,
            "variant": flag["defaultVariant"],
            "value": flag["variants"][flag["defaultVariant"]],
            "reason": "STATIC",
        }


# --- catalog ---------------------------------------------------------------


def test_catalog_has_every_v1_fault() -> None:
    ids = set(DEFINITIONS)
    assert len(ids) == 19
    assert {"payment_failure", "traffic_flood", "network_partition", "load_spike"} <= ids
    application = [d for d in DEFINITIONS.values() if d.category == FaultCategory.APPLICATION]
    infrastructure = [d for d in DEFINITIONS.values() if d.category == FaultCategory.INFRASTRUCTURE]
    assert len(application) == 13
    assert len(infrastructure) == 6


def test_catalog_uses_the_declared_flag_variants() -> None:
    assert DEFINITIONS["payment_failure"].variants == ["10%", "25%", "50%", "75%", "90%", "100%"]
    assert DEFINITIONS["cart_failure"].variants == ["10%", "25%", "50%", "75%", "90%", "100%"]
    assert DEFINITIONS["email_memory_leak"].variants == ["1x", "10x", "100x", "1000x", "10000x"]
    assert DEFINITIONS["shipping_slowdown"].variants == ["5sec", "10sec"]
    for definition in DEFINITIONS.values():
        if definition.mechanism == Mechanism.FLAGD:
            assert definition.flag
            assert definition.selectable_targets == [definition.target]


def test_catalog_response_carries_the_request_schema() -> None:
    response = catalog()
    assert response.version == 1
    assert response.faults
    assert "fault_id" in response.json_schema["properties"]
    assert "payment" in response.targets
    assert "postgres" not in response.targets


def test_resolve_rejects_unknown_fault() -> None:
    with pytest.raises(ApiError) as excinfo:
        resolve_request(FaultRequest(fault_id="nope"))
    assert excinfo.value.status_code == 404


def test_resolve_rejects_variant_outside_the_declared_set() -> None:
    with pytest.raises(ApiError) as excinfo:
        resolve_request(FaultRequest(fault_id="payment_failure", params={"variant": "37%"}))
    assert excinfo.value.status_code == 422
    assert excinfo.value.code == "invalid_variant"
    details = excinfo.value.details or {}
    assert details["allowed"] == DEFINITIONS["payment_failure"].variants


def test_resolve_rejects_non_lab_target_with_403() -> None:
    with pytest.raises(ApiError) as excinfo:
        resolve_request(FaultRequest(fault_id="service_down", target="postgres"))
    assert excinfo.value.status_code == 403
    assert excinfo.value.code == "unsafe_target"
    assert "postgres" in excinfo.value.message


def test_resolve_rejects_a_target_outside_the_fault() -> None:
    with pytest.raises(ApiError) as excinfo:
        resolve_request(FaultRequest(fault_id="payment_failure", target="cart"))
    assert excinfo.value.status_code == 422


def test_resolve_enforces_numeric_ranges() -> None:
    with pytest.raises(ApiError) as excinfo:
        resolve_request(FaultRequest(fault_id="cpu_pressure", target="ad", params={"cpus": 8}))
    assert excinfo.value.status_code == 422
    ok = resolve_request(FaultRequest(fault_id="cpu_pressure", target="ad", params={"cpus": 0.5}))
    assert ok.params == {"cpus": 0.5}
    assert ok.ttl_s == DEFINITIONS["cpu_pressure"].default_ttl_s


def test_resolve_rejects_unknown_params_and_ttl() -> None:
    with pytest.raises(ApiError):
        resolve_request(FaultRequest(fault_id="service_freeze", target="ad", params={"bogus": 1}))
    with pytest.raises(ApiError) as excinfo:
        resolve_request(FaultRequest(fault_id="service_freeze", target="ad", ttl_s=99999))
    assert excinfo.value.code == "invalid_ttl"


def test_load_spike_ttl_follows_duration() -> None:
    normalized = resolve_request(
        FaultRequest(fault_id="load_spike", params={"users": 20, "duration_s": 120})
    )
    assert normalized.target == "load-generator"
    assert normalized.ttl_s == 120


# --- safety ----------------------------------------------------------------


def _row(name: str, *, state: str = "running", project: str = "docock-demo") -> dict[str, Any]:
    return {
        "Names": name,
        "State": state,
        "Labels": f"com.docker.compose.project={project},com.docker.compose.service={name}",
    }


def _inspect_payload(
    name: str, *, project: str = "docock-demo", network: str = "opentelemetry-demo"
) -> dict[str, Any]:
    return {
        "Name": f"/{name}",
        "Config": {
            "Image": "demo:latest",
            "Labels": {
                "com.docker.compose.project": project,
                "com.docker.compose.service": name,
            },
        },
        "State": {"Status": "running", "Running": True, "Paused": False},
        "HostConfig": {"NanoCpus": 0, "Memory": 146800640, "MemorySwap": 293601280},
        "NetworkSettings": {
            "Networks": {network: {"Aliases": [name, "abc123"], "DNSNames": [name, "abc123"]}}
        },
    }


async def test_safety_rejects_unlisted_target_without_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("docker must not be called for a non-lab target")

    monkeypatch.setattr(safety, "docker_json", boom)
    with pytest.raises(ApiError) as excinfo:
        await safety.resolve_target("postgres")
    assert excinfo.value.status_code == 403


async def test_safety_rejects_a_container_without_the_project_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_json(*_args: Any, **_kwargs: Any) -> Any:
        return [_row("payment", project="other-project")]

    async def fake_inspect(*_args: Any, **_kwargs: Any) -> Any:
        return [_inspect_payload("payment", project="other-project")]

    monkeypatch.setattr(safety, "docker_json", fake_json)
    monkeypatch.setattr(safety, "docker_inspect", fake_inspect)
    with pytest.raises(ApiError) as excinfo:
        await safety.resolve_target("payment")
    assert excinfo.value.status_code == 403
    assert "docock-demo" in excinfo.value.message


async def test_safety_rejects_a_non_demo_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_json(*_args: Any, **_kwargs: Any) -> Any:
        return [_row("payment")]

    async def fake_inspect(*_args: Any, **_kwargs: Any) -> Any:
        return [_inspect_payload("payment", network="host")]

    monkeypatch.setattr(safety, "docker_json", fake_json)
    monkeypatch.setattr(safety, "docker_inspect", fake_inspect)
    with pytest.raises(ApiError) as excinfo:
        await safety.resolve_target("payment")
    assert excinfo.value.status_code == 403


async def test_safety_resolves_a_lab_container_with_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_json(*_args: Any, **_kwargs: Any) -> Any:
        return [_row("payment")]

    async def fake_inspect(*_args: Any, **_kwargs: Any) -> Any:
        return [_inspect_payload("payment")]

    monkeypatch.setattr(safety, "docker_json", fake_json)
    monkeypatch.setattr(safety, "docker_inspect", fake_inspect)
    target = await safety.resolve_target("payment")
    assert target.project == "docock-demo"
    assert target.network == "opentelemetry-demo"
    assert "payment" in target.network_aliases
    assert target.memory_limit_mb == 140


async def test_safety_allows_a_detached_container_only_for_revert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_json(*_args: Any, **_kwargs: Any) -> Any:
        return [_row("payment")]

    async def fake_inspect(*_args: Any, **_kwargs: Any) -> Any:
        payload = _inspect_payload("payment")
        payload["NetworkSettings"]["Networks"] = {}
        return [payload]

    monkeypatch.setattr(safety, "docker_json", fake_json)
    monkeypatch.setattr(safety, "docker_inspect", fake_inspect)
    with pytest.raises(ApiError):
        await safety.resolve_target("payment")
    target = await safety.resolve_target("payment", require_network=False)
    assert target.networks == []
    assert set(ALLOWED_TARGETS) >= {"payment", "frontend", "load-generator"}


# --- flagd writer ----------------------------------------------------------


async def test_flagd_write_is_atomic_utf8_without_bom_and_forward_mtime(
    tmp_path: Path,
) -> None:
    path = _flag_file(tmp_path, mtime=time.time() - 3600)
    before_mtime = path.stat().st_mtime
    writer = FlagdWriter(path=path, http=FakeOFREP(path))  # type: ignore[arg-type]

    state = await writer.set_variant("paymentFailure", "100%")

    assert state.before == "off"
    assert state.after == "100%"
    assert state.read_back is not None and state.read_back["variant"] == "100%"
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b'"defaultVariant": "100%"' in raw
    assert path.stat().st_mtime > before_mtime
    assert not list(tmp_path.glob("*.tmp"))


async def test_flagd_rejects_an_undeclared_variant_without_writing(tmp_path: Path) -> None:
    path = _flag_file(tmp_path)
    original = path.read_bytes()
    writer = FlagdWriter(path=path, http=FakeOFREP(path))  # type: ignore[arg-type]

    with pytest.raises(ApiError) as excinfo:
        await writer.set_variant("paymentFailure", "37%")

    assert excinfo.value.status_code == 422
    assert path.read_bytes() == original


async def test_flagd_revert_restores_the_pre_image(tmp_path: Path) -> None:
    path = _flag_file(tmp_path)
    writer = FlagdWriter(path=path, http=FakeOFREP(path))  # type: ignore[arg-type]
    await writer.set_variant("paymentFailure", "10%")
    assert writer.current_variants()["paymentFailure"] == "10%"

    await writer.set_variant("paymentFailure", "off")
    assert writer.current_variants()["paymentFailure"] == "off"


async def test_flagd_refuses_a_bom_on_read(tmp_path: Path) -> None:
    path = _flag_file(tmp_path)
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    writer = FlagdWriter(path=path, http=FakeOFREP(path))  # type: ignore[arg-type]
    # A BOM is stripped on read; the next write emits clean UTF-8.
    await writer.set_variant("paymentFailure", "10%")
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")


# --- symptom evaluation ----------------------------------------------------


def test_error_rate_symptom_uses_the_absolute_floor() -> None:
    spec = SymptomSpec(probe="error_rate", service="payment", threshold=0.02)
    assert symptom_satisfied(spec, ProbeReading(value=0.0), None) is False
    assert symptom_satisfied(spec, ProbeReading(value=0.05), None) is True
    assert symptom_satisfied(spec, ProbeReading(value=None), None) is False


def test_latency_symptom_is_relative_to_the_pre_injection_baseline() -> None:
    spec = SymptomSpec(probe="p95_latency", service="ad", baseline_multiplier=1.5)
    assert symptom_satisfied(spec, ProbeReading(value=120.0), 100.0) is False
    assert symptom_satisfied(spec, ProbeReading(value=160.0), 100.0) is True
    # Without a baseline the relative rule cannot fire.
    assert symptom_satisfied(spec, ProbeReading(value=999.0), None) is False


def test_container_state_symptoms() -> None:
    running = SymptomSpec(probe="container_running", service="email", comparator="<=", threshold=0)
    assert symptom_satisfied(running, ProbeReading(value=0.0), None) is True
    assert symptom_satisfied(running, ProbeReading(value=1.0), None) is False

    healthy = SymptomSpec(probe="container_health", service="cart", comparator=">=", threshold=1)
    assert symptom_satisfied(healthy, ProbeReading(value=1.0), None) is True

    detached = SymptomSpec(
        probe="network_attached", service="payment", comparator="<=", threshold=0
    )
    assert symptom_satisfied(detached, ProbeReading(value=0.0), None) is True


def test_limit_symptom_compares_against_the_pre_image() -> None:
    spec = SymptomSpec(probe="cpu_limit", service="ad", comparator=">", threshold=0)
    assert symptom_satisfied(spec, ProbeReading(value=0.25), None) is True
    assert symptom_satisfied(spec, ProbeReading(value=0.5), 1.0) is True
    assert symptom_satisfied(spec, ProbeReading(value=1.0), 1.0) is False


# --- audit -----------------------------------------------------------------


async def test_audit_appends_and_tails(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit" / "faults.jsonl")
    await log.append(
        AuditEntry(actor="tester", action="inject", fault_id="payment_failure", outcome="ACTIVE")
    )
    await log.append(
        AuditEntry(actor="tester", action="revert", fault_id="payment_failure", outcome="REVERTED")
    )
    entries = await log.tail(10)
    assert [entry.action for entry in entries] == ["inject", "revert"]
    assert entries[0].at.tzinfo is not None


async def test_audit_skips_a_torn_line(tmp_path: Path) -> None:
    path = tmp_path / "faults.jsonl"
    log = AuditLog(path)
    await log.append(AuditEntry(actor="a", action="inject", outcome="ACTIVE"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"torn": ')
    entries = await log.tail(10)
    assert len(entries) == 1


async def test_controller_rejects_a_non_lab_target_before_any_write() -> None:
    from app.faults.controller import FaultController

    controller = FaultController()
    with pytest.raises(ApiError) as excinfo:
        await controller.inject(FaultRequest(fault_id="service_down", target="postgres"))
    assert excinfo.value.status_code == 403


def test_mechanism_registry_covers_every_definition() -> None:
    from app.faults.catalog import Mechanism

    for definition in DEFINITIONS.values():
        assert definition.mechanism in set(Mechanism)
        assert definition.symptom.probe
        if definition.category == FaultCategory.INFRASTRUCTURE:
            assert definition.target in ALLOWED_TARGETS
