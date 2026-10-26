"""Background fault tasks: TTL watchdog, symptom observer, startup reconcile.

Both loops are deliberately dumb and restartable. State lives in Postgres, so a
crashed API resumes from the rows rather than from process memory — which is
what makes "kill the API mid-fault" a safe operation.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from app.config import settings
from app.faults.controller import FaultController, get_controller
from app.logging import get_logger

log = get_logger(__name__)


class FaultRuntime:
    def __init__(self, controller: FaultController | None = None) -> None:
        self.controller = controller or get_controller()
        self._tasks: list[asyncio.Task[None]] = []
        self.reconcile_actions: list[dict[str, Any]] = []
        self.watchdog_ticks = 0
        self.observer_ticks = 0
        self.last_error: str | None = None

    async def start(self) -> None:
        if not settings.fault_watchdog_enabled:
            log.info("fault_runtime_disabled")
            return
        try:
            self.reconcile_actions = await self.controller.reconcile()
        except Exception as exc:  # a DB/Docker hiccup must not stop the API booting
            self.last_error = str(exc)[:300]
            log.error("fault_reconcile_failed", error=self.last_error)
        self._tasks = [
            asyncio.create_task(self._watchdog_loop(), name="fault-watchdog"),
            asyncio.create_task(self._observer_loop(), name="fault-observer"),
        ]
        log.info("fault_runtime_started", reconciled=len(self.reconcile_actions))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []

    async def _watchdog_loop(self) -> None:
        while True:
            try:
                reverted = await self.controller.expire_due()
                self.watchdog_ticks += 1
                if reverted:
                    log.info("fault_watchdog_reverted", count=len(reverted))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)[:300]
                log.error("fault_watchdog_error", error=self.last_error)
            await asyncio.sleep(settings.fault_watchdog_interval_s)

    async def _observer_loop(self) -> None:
        while True:
            try:
                await self.controller.observe_once()
                self.observer_ticks += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)[:300]
                log.error("fault_observer_error", error=self.last_error)
            await asyncio.sleep(settings.fault_observer_interval_s)


__all__ = ["FaultRuntime"]
