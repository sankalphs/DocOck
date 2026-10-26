"""Re-embed every memory with the configured embedder (PLAN §12).

    uv run --project apps/api python scripts/reembed.py            # re-embed all
    uv run --project apps/api python scripts/reembed.py --backfill # + missing memories
    uv run --project apps/api python scripts/reembed.py --dry-run
    uv run --project apps/api python scripts/reembed.py --resummarize  # rebuild summaries

Run it after changing the embedding provider or model. It checks the
`memories.embedding` column dimension first, re-embeds each stored summary
(never raw logs), then records the embedder identity in `settings`
(`memory.embedding`) so the API's startup guard enables memory again. A running
API picks the change up on its next `GET /api/memory/status`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy import select

from app.db.models import EMBEDDING_DIMENSIONS, Incident, Memory, Setting
from app.db.session import SessionFactory, dispose_engine
from app.memory.service import SETTINGS_KEY, memory
from app.models.gateway import gateway


async def main(args: argparse.Namespace) -> int:
    async with SessionFactory() as session:
        await gateway.load(session)
        column = await memory.column_dims(session)
    identity = memory.identity()
    print(f"column vector({column}), code {EMBEDDING_DIMENSIONS}, embedder {identity}")
    if column != EMBEDDING_DIMENSIONS or identity["dimensions"] != EMBEDDING_DIMENSIONS:
        print("dimension mismatch: migrate memories.embedding before re-embedding")
        return 2

    embedder = gateway.embedder()
    async with SessionFactory() as session:
        rows = list((await session.execute(select(Memory).order_by(Memory.id))).scalars().all())
        print(f"{len(rows)} memories to re-embed")
        started = time.perf_counter()
        batch = max(1, args.batch)
        for index in range(0, len(rows), batch):
            chunk = rows[index : index + batch]
            vectors = await embedder.embed([row.summary for row in chunk], kind="document")
            vectors = embedder.check(vectors, EMBEDDING_DIMENSIONS)
            for row, vector in zip(chunk, vectors, strict=True):
                row.embedding = vector
                row.meta = {**(row.meta or {}), "embedding": identity}
        if args.dry_run:
            await session.rollback()
            print("dry run: nothing written")
            return 0
        setting = await session.get(Setting, SETTINGS_KEY)
        if setting is None:
            session.add(Setting(key=SETTINGS_KEY, value=identity))
        else:
            setting.value = identity
        await session.commit()
        print(f"re-embedded {len(rows)} in {time.perf_counter() - started:.1f}s")

    async with SessionFactory() as session:
        status = await memory.check(session)
    print(f"guard: ready={status.ready} reason={status.reason}")
    if args.resummarize and status.ready:
        async with SessionFactory() as session:
            refs = list(
                (
                    await session.execute(
                        select(Incident.public_id).join(Memory, Memory.incident_id == Incident.id)
                    )
                ).scalars()
            )
        for ref in refs:
            await memory.remember(ref, force=True)
        print(f"re-summarized {len(refs)} incident memories from their stored rows")
    if args.backfill and status.ready:
        stored = await memory.sweep()
        print(f"backfilled {stored} resolved incident(s)")
    await gateway.aclose()
    await dispose_engine()
    return 0 if status.ready else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backfill", action="store_true", help="also embed missing memories")
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument(
        "--resummarize", action="store_true", help="rebuild incident summaries, then embed"
    )
    sys.exit(asyncio.run(main(parser.parse_args())))
