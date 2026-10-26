"""Seed the memory store with hand-written signatures of the catalog faults.

    uv run --project apps/api python scripts/seed-memory.py           # (re)seed
    uv run --project apps/api python scripts/seed-memory.py --remove  # drop seeds

Seeds give a cold lab something to retrieve. They follow the PLAN §12 template,
have no incident (`incident_id` NULL), are marked `{"seed": true}`, and rank
below an equally similar real incident (`SEED_FACTOR`). Like every memory they
are hypotheses for the agent, never evidence. Re-running replaces the seeds.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy import delete

from app.db.models import EMBEDDING_DIMENSIONS, Memory
from app.db.session import SessionFactory, dispose_engine
from app.memory.service import memory
from app.models.gateway import gateway

# (fault, root, fault_class, services, summary)
SEEDS: list[tuple[str, str, str, list[str], str]] = [
    (
        "payment_failure",
        "payment",
        "application_error",
        ["payment", "checkout", "frontend"],
        "payment. application_error. error rate breach at checkout with frontend, "
        "frontend-proxy.\nOnset: payment errors preceded checkout.\n"
        "Container: healthy. Restart: not attempted.\n"
        "Action: reset_fault on payment. Verification: charges succeed again.",
    ),
    (
        "catalog_lock_contention",
        "product-catalog",
        "application_error",
        ["product-catalog", "frontend", "frontend-proxy"],
        "product-catalog. application_error. latency breach at frontend-proxy with "
        "frontend.\nOnset: product-catalog latency preceded frontend.\n"
        "Container: healthy. Restart: not attempted.\n"
        "Action: reset_fault on product-catalog. Verification: latency back to baseline.",
    ),
    (
        "payment_unreachable",
        "payment",
        "configuration",
        ["payment", "checkout"],
        "payment. configuration. error rate breach at checkout with frontend-proxy.\n"
        "Onset: checkout errors with no payment spans.\n"
        "Container: healthy. Restart: not attempted.\n"
        "Action: reset_fault on payment. Verification: checkout reaches payment again.",
    ),
    (
        "image_slow_load",
        "frontend",
        "application_error",
        ["frontend", "frontend-proxy", "image-provider"],
        "frontend. application_error. latency breach at frontend-proxy.\n"
        "Onset: frontend latency preceded frontend-proxy.\n"
        "Container: healthy. Restart: not attempted.\n"
        "Action: reset_fault on frontend. Verification: image requests fast again.",
    ),
    (
        "service_down",
        "email",
        "dependency_unavailable",
        ["email", "checkout"],
        "email. dependency_unavailable. error rate breach at checkout.\n"
        "Onset: email stopped answering before checkout errors.\n"
        "Container: crashed. Restart: helped.\n"
        "Action: restart_service on email. Verification: confirmation emails sent again.",
    ),
    (
        "cpu_pressure",
        "ad",
        "resource_exhaustion",
        ["ad", "frontend"],
        "ad. resource_exhaustion. latency breach at ad with frontend.\n"
        "Onset: ad latency preceded frontend.\n"
        "Container: limited. Restart: not attempted.\n"
        "Action: remove_resource_limit on ad. Verification: ad latency back to baseline.",
    ),
    (
        "network_partition",
        "payment",
        "network",
        ["payment", "checkout"],
        "payment. network. telemetry silence at payment with checkout.\n"
        "Onset: payment went silent before checkout errors.\n"
        "Container: detached. Restart: not attempted.\n"
        "Action: restore_network on payment. Verification: payment spans resume.",
    ),
    (
        "traffic_flood",
        "frontend",
        "traffic",
        ["frontend", "frontend-proxy"],
        "frontend. traffic. throughput anomaly at frontend-proxy with frontend.\n"
        "Onset: frontend request rate rose first.\n"
        "Container: healthy. Restart: not attempted.\n"
        "Action: clear_feature_flag on frontend. Verification: request rate back to baseline.",
    ),
    (
        "service_freeze",
        "cart",
        "dependency_unavailable",
        ["cart", "frontend"],
        "cart. dependency_unavailable. latency breach at frontend with frontend-proxy.\n"
        "Onset: cart stopped answering before frontend timeouts.\n"
        "Container: paused. Restart: helped.\n"
        "Action: restart_service on cart. Verification: cart calls succeed again.",
    ),
    (
        "memory_pressure",
        "recommendation",
        "resource_exhaustion",
        ["recommendation", "frontend"],
        "recommendation. resource_exhaustion. error rate breach at frontend.\n"
        "Onset: recommendation errors preceded frontend.\n"
        "Container: oom. Restart: helped.\n"
        "Action: remove_resource_limit on recommendation. Verification: no further kills.",
    ),
]


async def main(args: argparse.Namespace) -> int:
    async with SessionFactory() as session:
        await gateway.load(session)
        status = await memory.check(session)
        removed = await session.execute(delete(Memory).where(Memory.incident_id.is_(None)))
        print(f"removed {removed.rowcount} existing seed(s)")  # type: ignore[attr-defined]
        if args.remove:
            await session.commit()
            return 0
        if not status.ready:
            await session.rollback()
            print(f"memory disabled: {status.reason}")
            return 1
        embedder = gateway.embedder()
        vectors = await embedder.embed([seed[4] for seed in SEEDS], kind="document")
        vectors = embedder.check(vectors, EMBEDDING_DIMENSIONS)
        for (fault, root, fault_class, services, summary), vector in zip(
            SEEDS, vectors, strict=True
        ):
            session.add(
                Memory(
                    incident_id=None,
                    summary=summary,
                    embedding=vector,
                    root_cause=root,
                    fault_class=fault_class,
                    services=services,
                    meta={
                        "seed": True,
                        "fault": fault,
                        "source": "scripts/seed-memory.py",
                        "embedding": memory.identity(),
                    },
                )
            )
        await session.commit()
    print(f"seeded {len(SEEDS)} memories with {memory.identity()}")
    await gateway.aclose()
    await dispose_engine()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true", help="only delete the seeds")
    sys.exit(asyncio.run(main(parser.parse_args())))
