"""Log templating: normalize messages, then find templates that are new.

Raw log text is never stored as evidence. Numbers, ids, UUIDs, addresses, and
durations are replaced with placeholders; the incident keeps the template and
the count, plus one example line. "New" means present in the incident window
and absent from the pre-incident window for the same service.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from app.telemetry.logs import LogsAdapter

UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)
HEX_RE = re.compile(r"\b[0-9a-f]{16,32}\b", re.IGNORECASE)
IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")
NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")
DURATION_RE = re.compile(r"\b\d+(?:\.\d+)?\s?(ms|s|us|ns|mib|mb|gb)\b", re.IGNORECASE)
QUOTED_RE = re.compile(r"(['\"])[^'\"]{8,}\1")


def template_of(text: str) -> str:
    normalized = text.strip()
    normalized = UUID_RE.sub("<uuid>", normalized)
    normalized = IP_RE.sub("<ip>", normalized)
    normalized = HEX_RE.sub("<hex>", normalized)
    normalized = DURATION_RE.sub("<duration>", normalized)
    normalized = QUOTED_RE.sub("<quoted>", normalized)
    normalized = NUMBER_RE.sub("<n>", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized[:240]


class LogTemplate(BaseModel):
    service: str
    template: str
    count: int
    is_new: bool = True
    sample: str = ""


class LogAnalysis(BaseModel):
    templates: list[LogTemplate] = Field(default_factory=list)
    totals: dict[str, int] = Field(default_factory=dict)
    scanned: int = 0
    new_templates: int = 0


def _timestamp(record: Any) -> datetime | None:
    raw = getattr(record, "timestamp", None)
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


async def analyse_logs(
    logs: LogsAdapter,
    *,
    opened_at: datetime,
    end: datetime,
    services: list[str],
    per_service: int = 100,
    max_per_service: int = 5,
) -> LogAnalysis:
    analysis = LogAnalysis()
    if not services:
        return analysis
    # Fixed window (5 minutes before the opening to the incident's end) so a
    # re-run on a closed incident reads the same records.
    window_start = opened_at - timedelta(seconds=300)
    window_floor = opened_at - timedelta(seconds=60)
    new_templates: list[LogTemplate] = []

    for service in services[:6]:
        try:
            result = await logs.search(
                service=service, start=window_start, end=end, limit=per_service
            )
        except Exception:
            continue
        analysis.scanned += len(result.records)
        pre: set[str] = set()
        window: dict[str, dict[str, Any]] = {}
        for record in result.records:
            at = _timestamp(record)
            if at is None:
                continue
            normalized = template_of(record.body)
            if at < window_floor:
                pre.add(normalized)
                continue
            bucket = window.setdefault(
                normalized, {"count": 0, "sample": record.body[:400], "severity": record.severity}
            )
            bucket["count"] += 1
        analysis.totals[service] = sum(bucket["count"] for bucket in window.values())
        fresh = [
            LogTemplate(
                service=service,
                template=normalized,
                count=int(bucket["count"]),
                sample=str(bucket["sample"]),
            )
            for normalized, bucket in window.items()
            if normalized not in pre
        ]
        fresh.sort(key=lambda item: item.count, reverse=True)
        new_templates.extend(fresh[:max_per_service])

    new_templates.sort(key=lambda item: item.count, reverse=True)
    analysis.templates = new_templates[:15]
    analysis.new_templates = len(analysis.templates)
    return analysis


__all__ = ["LogAnalysis", "LogTemplate", "analyse_logs", "template_of"]
