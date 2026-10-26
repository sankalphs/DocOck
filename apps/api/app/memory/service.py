"""Memory store and retrieval (PLAN §12).

- **Store only after resolution.** A consumer on the event bus embeds an incident
  when it reaches RESOLVED and has a valid diagnosis; a startup sweep catches
  anything missed (API down, commit race). One memory per incident.
- **Dimension guard.** At startup the `memories.embedding` column dimension is
  read from Postgres and compared with the code and the configured embedder;
  the embedder identity is stored in `settings` (`memory.embedding`). A
  mismatch disables memory (no writes, no retrieval) until
  `scripts/reembed.py` runs — vectors from two models are never mixed.
- **Hybrid retrieval.** pgvector cosine candidates (HNSW) above the threshold,
  plus structured candidates whose root cause is a current suspect; scored as
  cosine + lexical overlap + recency, filtered by fault class when known.
- **Hypotheses only.** Results go to the agent as hypotheses; they carry no
  evidence codes and the diagnosis schema has no field for them.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.validator import canonical
from app.db.models import (
    EMBEDDING_DIMENSIONS,
    Action,
    AgentRun,
    Diagnosis,
    Evidence,
    Incident,
    IncidentEvent,
    Memory,
    Setting,
    Suspect,
    Verification,
)
from app.db.session import SessionFactory
from app.domain.state import IncidentStatus
from app.errors import ApiError
from app.events.bus import BusEvent, bus
from app.logging import get_logger
from app.memory.summarizer import build_facts, query_text
from app.models.gateway import ModelGateway
from app.models.gateway import gateway as default_gateway

log = get_logger(__name__)

SETTINGS_KEY = "memory.embedding"
CANDIDATES = 20
W_COSINE, W_LEXICAL, W_RECENCY = 0.75, 0.15, 0.10
RECENCY_HALF_LIFE_DAYS = 7.0
# Seeds are hand-written, not observed: a real resolved incident outranks them.
SEED_FACTOR = 0.9
_TOKEN = re.compile(r"[a-z][a-z\-_]+")


class MemoryHit(BaseModel):
    memory_id: int
    incident_ref: str | None
    root_cause: str
    fault_class: str
    summary: str
    services: list[str]
    seed: bool
    cosine: float
    lexical: float
    recency: float
    score: float
    created_at: datetime


class MemoryStatus(BaseModel):
    ready: bool
    reason: str | None
    column_dimensions: int | None
    expected_dimensions: int
    stored: dict[str, Any] | None
    current: dict[str, Any]
    memories: int
    seeds: int


class MemorySearchResult(BaseModel):
    query: str
    fault_class: str | None
    top_k: int
    min_score: float
    hits: list[MemoryHit]


def _tokens(value: str) -> set[str]:
    return set(_TOKEN.findall(value.lower()))


def lexical_overlap(query: str, summary: str) -> float:
    a, b = _tokens(query), _tokens(summary)
    return len(a & b) / len(a | b) if a and b else 0.0


def recency_weight(created_at: datetime, now: datetime) -> float:
    age_days = max(0.0, (now - created_at).total_seconds() / 86400)
    return math.pow(0.5, age_days / RECENCY_HALF_LIFE_DAYS)


@dataclass
class MemoryService:
    gateway: ModelGateway = field(default_factory=lambda: default_gateway)
    ready: bool = False
    reason: str | None = "not checked yet"
    column_dimensions: int | None = None
    _queue: asyncio.Queue[BusEvent] | None = None
    _task: asyncio.Task[None] | None = None
    _pending: set[asyncio.Task[None]] = field(default_factory=set)

    # --- identity / guard ---------------------------------------------------------
    def identity(self) -> dict[str, Any]:
        cfg = self.gateway.config.embedding
        return {"provider": cfg.provider, "model": cfg.model, "dimensions": cfg.dimensions}

    async def column_dims(self, session: AsyncSession) -> int | None:
        value = await session.scalar(
            text(
                "SELECT atttypmod FROM pg_attribute "
                "WHERE attrelid = 'memories'::regclass AND attname = 'embedding'"
            )
        )
        return int(value) if value is not None and int(value) > 0 else None

    async def check(self, session: AsyncSession) -> MemoryStatus:
        """The startup dimension guard. Never raises: it disables memory instead."""
        self.column_dimensions = await self.column_dims(session)
        current = self.identity()
        row = await session.get(Setting, SETTINGS_KEY)
        stored = dict(row.value) if row else None
        count = int(await session.scalar(select(func.count()).select_from(Memory)) or 0)
        reason: str | None = None
        if self.column_dimensions != EMBEDDING_DIMENSIONS:
            reason = (
                f"memories.embedding is vector({self.column_dimensions}) but the code "
                f"expects {EMBEDDING_DIMENSIONS}; run the migrations"
            )
        elif current["dimensions"] != EMBEDDING_DIMENSIONS:
            reason = f"embedder dimensions {current['dimensions']} != column {EMBEDDING_DIMENSIONS}"
        elif stored is None or count == 0:
            # Empty store (or first start): adopt the current embedder.
            if row is None:
                session.add(Setting(key=SETTINGS_KEY, value=current))
            else:
                row.value = current
            await session.commit()
            stored = current
        elif stored != current:
            reason = (
                f"memories were embedded with {stored.get('provider')}:{stored.get('model')} "
                f"({stored.get('dimensions')}-d) but the embedder is now "
                f"{current['provider']}:{current['model']}; run scripts/reembed.py"
            )
        self.ready, self.reason = reason is None, reason
        if reason:
            log.error("memory_guard_failed", reason=reason)
        else:
            log.info("memory_guard_ok", dimensions=self.column_dimensions, memories=count)
        seeds = int(
            await session.scalar(
                select(func.count()).select_from(Memory).where(Memory.incident_id.is_(None))
            )
            or 0
        )
        return MemoryStatus(
            ready=self.ready,
            reason=reason,
            column_dimensions=self.column_dimensions,
            expected_dimensions=EMBEDDING_DIMENSIONS,
            stored=stored,
            current=current,
            memories=count,
            seeds=seeds,
        )

    async def status(self) -> MemoryStatus:
        async with SessionFactory() as session:
            return await self.check(session)

    def _require_ready(self) -> None:
        if not self.ready:
            raise ApiError(409, "memory_unavailable", self.reason or "memory is disabled")

    # --- writes ------------------------------------------------------------------
    async def remember(self, ref: str, *, force: bool = False) -> Memory | None:
        """Summarize and embed one RESOLVED incident. Idempotent."""
        self._require_ready()
        async with SessionFactory() as session:
            incident = await session.scalar(select(Incident).where(Incident.public_id == ref))
            if incident is None or incident.status != IncidentStatus.RESOLVED:
                return None
            existing = await session.scalar(select(Memory).where(Memory.incident_id == incident.id))
            if existing is not None and not force:
                return existing
            diagnosis = await session.scalar(
                select(Diagnosis)
                .join(AgentRun, AgentRun.id == Diagnosis.run_id)
                .where(AgentRun.incident_id == incident.id, Diagnosis.valid.is_(True))
                .order_by(Diagnosis.id.desc())
            )
            if diagnosis is None:
                # No verified root cause, nothing worth remembering.
                return None
            evidence = list(
                (
                    await session.execute(
                        select(Evidence)
                        .where(Evidence.incident_id == incident.id)
                        .order_by(Evidence.code)
                    )
                )
                .scalars()
                .all()
            )
            closing = await session.scalar(
                select(IncidentEvent)
                .where(IncidentEvent.incident_id == incident.id)
                .order_by(IncidentEvent.seq.desc())
            )
            # P15: the action that actually ran and its verified outcome beat
            # the diagnosis' recommendation and the closing event.
            executed = (
                await session.execute(
                    select(Action, Verification)
                    .outerjoin(Verification, Verification.action_row_id == Action.id)
                    .where(Action.incident_id == incident.id, Action.started_at.is_not(None))
                    .order_by(Action.started_at.desc(), Verification.id.desc().nulls_last())
                    .limit(1)
                )
            ).first()
            action = (
                {"action_id": executed[0].action_id, "target": executed[0].target}
                if executed is not None
                else diagnosis.recommended_action
            )
            verification = (
                _verified(executed[1])
                if executed is not None and executed[1] is not None
                else _verdict(closing)
            )
            facts = build_facts(
                incident,
                evidence,
                root=diagnosis.root_cause_service,
                fault_class=diagnosis.fault_class,
                action=action,
                affected=list(diagnosis.affected_services or []),
                verification=verification,
            )
            summary = facts.text()
            vector = await self.gateway.embedder().embed_one(summary, kind="document")
            meta = {
                "incident_ref": incident.public_id,
                "diagnosis_id": diagnosis.id,
                "run_id": diagnosis.run_id,
                "confidence": diagnosis.confidence,
                "action": action,
                "verification": (
                    executed[1].verdict
                    if executed is not None and executed[1] is not None
                    else None
                ),
                "resolved_at": incident.closed_at.isoformat() if incident.closed_at else None,
                "embedding": self.identity(),
            }
            if existing is not None:
                existing.summary, existing.embedding = summary, vector
                existing.root_cause, existing.fault_class = facts.root, facts.fault_class
                existing.services, existing.meta = facts.services, meta
                row = existing
            else:
                row = Memory(
                    incident_id=incident.id,
                    summary=summary,
                    embedding=vector,
                    root_cause=facts.root,
                    fault_class=facts.fault_class,
                    services=facts.services,
                    meta=meta,
                )
                session.add(row)
            await session.commit()
            await session.refresh(row)
        bus.publish(
            "memory.stored",
            {"incident_id": ref, "memory_id": row.id, "root_cause": row.root_cause},
        )
        log.info("memory_stored", incident=ref, memory_id=row.id, root=row.root_cause)
        return row

    async def sweep(self) -> int:
        """Embed every RESOLVED incident with a valid diagnosis and no memory."""
        if not self.ready:
            return 0
        async with SessionFactory() as session:
            refs = (
                await session.execute(
                    select(Incident.public_id)
                    .join(AgentRun, AgentRun.incident_id == Incident.id)
                    .join(Diagnosis, Diagnosis.run_id == AgentRun.id)
                    .outerjoin(Memory, Memory.incident_id == Incident.id)
                    .where(
                        Incident.status == IncidentStatus.RESOLVED,
                        Diagnosis.valid.is_(True),
                        Memory.id.is_(None),
                    )
                    .distinct()
                )
            ).scalars()
            pending = list(refs)
        stored = 0
        for ref in pending:
            try:
                if await self.remember(ref):
                    stored += 1
            except Exception as exc:
                log.warning("memory_sweep_failed", incident=ref, error=str(exc))
        return stored

    # --- retrieval ---------------------------------------------------------------
    async def search(
        self,
        query: str,
        *,
        fault_class: str | None = None,
        suspects: list[str] | None = None,
        exclude_incident: int | None = None,
        top_k: int | None = None,
        min_score: float | None = None,
    ) -> MemorySearchResult:
        self._require_ready()
        cfg = self.gateway.config.retrieval
        k = top_k or cfg.top_k
        threshold = cfg.min_score if min_score is None else min_score
        vector = await self.gateway.embedder().embed_one(query, kind="query")
        async with SessionFactory() as session:
            distance = Memory.embedding.cosine_distance(vector)
            base = select(Memory, Incident.public_id, (1 - distance).label("cosine")).outerjoin(
                Incident, Incident.id == Memory.incident_id
            )
            if exclude_incident is not None:
                base = base.where(
                    (Memory.incident_id.is_(None)) | (Memory.incident_id != exclude_incident)
                )
            if fault_class:
                base = base.where(Memory.fault_class == fault_class)
            rows = list((await session.execute(base.order_by(distance).limit(CANDIDATES))).all())
            if suspects:
                seen = {memory.id for memory, _ref, _cos in rows}
                structured = (
                    await session.execute(
                        base.where(Memory.root_cause.in_([canonical(s) for s in suspects]))
                        .order_by(Memory.created_at.desc())
                        .limit(CANDIDATES)
                    )
                ).all()
                rows += [row for row in structured if row[0].id not in seen]
        now = datetime.now(UTC)
        hits: list[MemoryHit] = []
        for memory, ref, cosine in rows:
            cosine = float(cosine)
            if cosine < threshold:
                continue
            created = (
                memory.created_at
                if memory.created_at.tzinfo
                else memory.created_at.replace(tzinfo=UTC)
            )
            lexical = lexical_overlap(query, memory.summary)
            recency = recency_weight(created, now)
            score = W_COSINE * cosine + W_LEXICAL * lexical + W_RECENCY * recency
            seed = memory.incident_id is None
            if seed:
                score *= SEED_FACTOR
            hits.append(
                MemoryHit(
                    memory_id=memory.id,
                    incident_ref=ref,
                    root_cause=memory.root_cause,
                    fault_class=memory.fault_class,
                    summary=memory.summary,
                    services=list(memory.services or []),
                    seed=seed,
                    cosine=round(cosine, 4),
                    lexical=round(lexical, 4),
                    recency=round(recency, 4),
                    score=round(score, 4),
                    created_at=created,
                )
            )
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return MemorySearchResult(
            query=query, fault_class=fault_class, top_k=k, min_score=threshold, hits=hits[:k]
        )

    async def incident_query(
        self, session: AsyncSession, incident: Incident, *, lead: str | None = None
    ) -> tuple[str, list[str], str | None]:
        suspects = list(
            (
                await session.execute(
                    select(Suspect.service)
                    .where(Suspect.incident_id == incident.id)
                    .order_by(Suspect.rank)
                    .limit(3)
                )
            ).scalars()
        )
        evidence = list(
            (
                await session.execute(
                    select(Evidence)
                    .where(Evidence.incident_id == incident.id)
                    .order_by(Evidence.code)
                )
            )
            .scalars()
            .all()
        )
        lead = lead or (suspects[0] if suspects else (incident.primary_service or "unknown"))
        query = query_text(incident, evidence, lead=lead)
        # Only a direct container observation is confident enough to filter on.
        container_line = query.rsplit("Container: ", 1)[-1].rstrip(".")
        fault_class = {
            "crashed": "dependency_unavailable",
            "paused": "dependency_unavailable",
            "detached": "network",
            "oom": "resource_exhaustion",
        }.get(container_line)
        return query, suspects, fault_class

    async def search_incident(
        self, ref: str, *, top_k: int | None = None, lead: str | None = None
    ) -> MemorySearchResult:
        async with SessionFactory() as session:
            incident = await session.scalar(select(Incident).where(Incident.public_id == ref))
            if incident is None:
                raise ApiError(404, "incident_not_found", f"incident {ref} not found")
            query, suspects, fault_class = await self.incident_query(session, incident, lead=lead)
        return await self.search(
            query,
            fault_class=fault_class,
            suspects=suspects,
            exclude_incident=incident.id,
            top_k=top_k,
        )

    async def hypotheses(self, session: AsyncSession, incident: Incident) -> list[dict[str, Any]]:
        """Context items for the agent. Empty when memory is disabled."""
        if not self.ready:
            return []
        try:
            query, suspects, fault_class = await self.incident_query(session, incident)
            result = await self.search(
                query, fault_class=fault_class, suspects=suspects, exclude_incident=incident.id
            )
        except Exception as exc:  # memory is advisory: never block an investigation
            log.warning("memory_retrieval_failed", incident=incident.public_id, error=str(exc))
            return []
        return [hypothesis(hit) for hit in result.hits]

    # --- runtime ------------------------------------------------------------------
    async def start(self) -> None:
        try:
            async with SessionFactory() as session:
                await self.check(session)
            swept = await self.sweep()
            if swept:
                log.info("memory_sweep", stored=swept)
        except Exception as exc:
            self.ready, self.reason = False, f"memory check failed: {exc}"
            log.error("memory_start_failed", error=str(exc))
        self._queue = bus.subscribe()
        self._task = asyncio.create_task(self._consume(), name="memory-consumer")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._queue is not None:
            bus.unsubscribe(self._queue)
        for task in list(self._pending):
            task.cancel()

    async def _consume(self) -> None:
        assert self._queue is not None
        while True:
            event = await self._queue.get()
            data = event.data or {}
            resolved = (
                event.type == "incident.closed" and data.get("status") == IncidentStatus.RESOLVED
            ) or (event.type == "incident.transition" and data.get("to") == IncidentStatus.RESOLVED)
            if resolved and data.get("incident_id"):
                task = asyncio.create_task(self._remember_later(str(data["incident_id"])))
                self._pending.add(task)
                task.add_done_callback(self._pending.discard)

    async def _remember_later(self, ref: str) -> None:
        # The event is published before the caller commits; retry until visible.
        for delay in (0.5, 2.0, 5.0, 15.0):
            await asyncio.sleep(delay)
            try:
                if await self.remember(ref):
                    return
            except ApiError:
                return  # memory disabled; the guard already logged why
            except Exception as exc:
                log.warning("memory_store_failed", incident=ref, error=str(exc))


def _verdict(event: IncidentEvent | None) -> str:
    if event is None:
        return "unknown"
    if event.kind == "recovered":
        return "symptoms cleared and the detector resolved it"
    payload = event.payload or {}
    if payload.get("to") == IncidentStatus.RESOLVED:
        return f"resolved by {payload.get('actor') or 'system'}"
    return event.kind


def _verified(row: Verification) -> str:
    """`PASSED after 52 s (symptoms_clear, faults_reverted, flag_off)`."""
    final = (row.criteria or {}).get("final") or []
    held = [str(c.get("id")) for c in final if c.get("ok")]
    failed = [str(c.get("id")) for c in final if not c.get("ok")]
    names = ", ".join(failed if failed else held)
    return f"{row.verdict} after {row.observed_for_s} s" + (f" ({names})" if names else "")


def hypothesis(hit: MemoryHit) -> dict[str, Any]:
    """What the agent sees. No evidence codes; the ref is a past incident id."""
    return {
        "past_incident": hit.incident_ref or f"seed-{hit.memory_id}",
        "root_cause": hit.root_cause,
        "fault_class": hit.fault_class,
        "similarity": hit.score,
        "summary": hit.summary,
    }


memory = MemoryService()

__all__ = [
    "MemoryHit",
    "MemorySearchResult",
    "MemoryService",
    "MemoryStatus",
    "hypothesis",
    "lexical_overlap",
    "memory",
    "recency_weight",
]
