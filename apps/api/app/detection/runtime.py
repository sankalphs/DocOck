"""Detector task: one loop, one cycle every `detector_interval_s`."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from app.config import settings
from app.db.session import SessionFactory
from app.detection.detector import Detector
from app.logging import get_logger

log = get_logger(__name__)

PERSIST_EVERY_CYCLES = 6  # one minute at a 10 s cadence


class DetectionRuntime:
    def __init__(self, detector: Detector | None = None) -> None:
        self.detector = detector or Detector()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not settings.detector_enabled:
            log.info("detector_disabled")
            return
        try:
            async with SessionFactory() as session:
                await self.detector.store.load(session)
            await self.detector.backfill()
        except Exception as exc:  # Prometheus/DB may still be warming up
            self.detector.last_error = str(exc)[:300]
            log.warning("detector_startup_incomplete", error=self.detector.last_error)
        self._task = asyncio.create_task(self._loop(), name="incident-detector")
        log.info(
            "detector_started",
            interval_s=settings.detector_interval_s,
            window=settings.detector_window,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                persist = self.detector._cycles % PERSIST_EVERY_CYCLES == 0
                await self.detector.run_once(persist=persist)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.detector.last_error = str(exc)[:300]
                log.error("detector_cycle_failed", error=self.detector.last_error)
            await asyncio.sleep(settings.detector_interval_s)

    def status(self) -> dict[str, Any]:
        status = self.detector.status()
        status["task_running"] = self._task is not None and not self._task.done()
        return status


runtime = DetectionRuntime()

__all__ = ["DetectionRuntime", "runtime"]
