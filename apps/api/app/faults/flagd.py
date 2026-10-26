"""flagd writer — the only code that mutates demo configuration.

Protocol (PLAN §8):

1. Read the runtime file, set `flags[key].defaultVariant`, reject variants that
   are not declared.
2. Write a temp file in the same directory as UTF-8 **without BOM**, then
   `os.replace` (atomic).
3. Move the mtime forward; flagd ignores a file whose timestamp went backwards.
4. Poll OFREP until the evaluation reflects the change; on timeout restart flagd
   and check once more.
5. All writes are serialised behind one async lock and carry a pre-image.

The file is `runtime/flagd/demo.flagd.json`; the upstream checkout is never
written.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from app.config import settings
from app.errors import ApiError
from app.logging import get_logger
from app.telemetry.docker import DockerError, run_docker
from app.telemetry.http import AsyncHttpClient, shared_client

log = get_logger(__name__)

BOM = b"\xef\xbb\xbf"


class FlagState(BaseModel):
    key: str
    before: str
    after: str
    wrote_at: datetime
    file: str
    read_back: dict[str, Any] | None = None


class FlagdWriteError(Exception):
    pass


class FlagdWriter:
    def __init__(
        self,
        *,
        path: Path | None = None,
        ofrep_url: str | None = None,
        http: AsyncHttpClient | None = None,
    ) -> None:
        self.path = path or settings.flagd_flags_file
        self._http = http or shared_client(
            "flagd-ofrep", base_url=ofrep_url or settings.flagd_ofrep_url, timeout_s=5.0
        )
        self._lock = asyncio.Lock()

    # --- reads -------------------------------------------------------------
    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            raise FlagdWriteError(f"flag file does not exist: {self.path}")
        raw = self.path.read_bytes()
        if raw.startswith(BOM):
            # Self-heal a seed copy that preserved a BOM; flagd cannot parse it.
            raw = raw[len(BOM) :]
        return json.loads(raw.decode("utf-8"))

    def current_variants(self) -> dict[str, str]:
        data = self._read()
        return {
            key: str(flag.get("defaultVariant") or "off")
            for key, flag in (data.get("flags") or {}).items()
        }

    async def evaluate(self, key: str) -> dict[str, Any]:
        try:
            payload = await self._http.post_json(f"/ofrep/v1/evaluate/flags/{key}", json={})
        except Exception as exc:
            raise FlagdWriteError(f"OFREP evaluation failed for {key}: {exc}") from exc
        return dict(payload)

    async def wait_for(self, key: str, variant: str, *, timeout_s: float = 15.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            try:
                last = await self.evaluate(key)
            except FlagdWriteError as exc:
                last = {"error": str(exc)}
            if last.get("variant") == variant:
                return last
            await asyncio.sleep(0.25)
        return last

    # --- writes ------------------------------------------------------------
    async def set_variant(self, key: str, variant: str) -> FlagState:
        async with self._lock:
            state = await asyncio.to_thread(self._write, key, variant)
            read_back = await self.wait_for(key, variant)
            if read_back.get("variant") != variant:
                log.warning("flagd_ofrep_stale", flag=key, expected=variant, seen=read_back)
                await self._restart_flagd()
                read_back = await self.wait_for(key, variant, timeout_s=20.0)
            if read_back.get("variant") != variant:
                raise ApiError(
                    502,
                    "flagd_not_applied",
                    f"flagd did not apply {key}={variant} within the read-back window",
                    {"seen": read_back},
                )
            state.read_back = read_back
            log.info(
                "flagd_write",
                flag=key,
                before=state.before,
                after=variant,
                read_back=read_back.get("reason"),
            )
            return state

    def _write(self, key: str, variant: str) -> FlagState:
        data = self._read()
        flags = data.get("flags")
        if not isinstance(flags, dict) or key not in flags:
            raise ApiError(404, "unknown_flag", f"Flag '{key}' is not in {self.path.name}")
        flag = flags[key]
        declared = flag.get("variants") or {}
        if variant not in declared:
            raise ApiError(
                422,
                "invalid_variant",
                f"'{variant}' is not declared for flag {key}",
                {"allowed": sorted(declared)},
            )

        before = str(flag.get("defaultVariant") or "off")
        if before == variant:
            return FlagState(
                key=key,
                before=before,
                after=variant,
                wrote_at=datetime.now(UTC),
                file=str(self.path),
            )

        flag["defaultVariant"] = variant
        self._atomic_write(data)
        return FlagState(
            key=key,
            before=before,
            after=variant,
            wrote_at=datetime.now(UTC),
            file=str(self.path),
        )

    def _atomic_write(self, data: dict[str, Any]) -> None:
        previous_mtime = self.path.stat().st_mtime if self.path.exists() else 0.0
        payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        tmp = self.path.with_name(f"{self.path.name}.{uuid4().hex}.tmp")
        try:
            # encoding="utf-8" never emits a BOM; newline="\n" keeps the bytes
            # stable across Windows/Linux.
            with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

        now = time.time()
        if now <= previous_mtime:
            # Filesystems with coarse timestamps can hand back the same second;
            # flagd ignores a non-advancing mtime.
            os.utime(self.path, (previous_mtime + 0.01, previous_mtime + 0.01))
        else:
            os.utime(self.path, (now, now))

        head = self.path.read_bytes()[: len(BOM)]
        if head == BOM:
            raise FlagdWriteError("refusing to hand flagd a UTF-8 BOM")

    async def _restart_flagd(self) -> None:
        try:
            await run_docker("restart", "flagd", timeout_s=40.0)
        except DockerError as exc:
            log.error("flagd_restart_failed", error=str(exc))


async def reset_drift(writer: FlagdWriter, *, target_variant: str = "off") -> list[dict[str, Any]]:
    """Put every catalog flag back to `off` (panic-button drift sweep)."""
    from app.faults.catalog import DEFINITIONS

    flags = sorted(
        {definition.flag for definition in DEFINITIONS.values() if definition.flag is not None}
    )
    changed: list[dict[str, Any]] = []
    for flag in flags:
        current = writer.current_variants().get(flag)
        if current is not None and current != target_variant:
            try:
                state = await writer.set_variant(flag, target_variant)
            except ApiError as exc:
                changed.append({"flag": flag, "before": current, "error": exc.message})
                continue
            changed.append({"flag": flag, "before": state.before, "after": target_variant})
    return changed


__all__ = ["FlagState", "FlagdWriteError", "FlagdWriter", "reset_drift"]
