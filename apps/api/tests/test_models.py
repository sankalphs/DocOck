from __future__ import annotations

from pgvector.sqlalchemy import Vector
from sqlalchemy import UniqueConstraint

import app.db.models  # noqa: F401  (registers the mappers)
from app.db.base import Base

EXPECTED_TABLES = {
    "actions",
    "agent_runs",
    "agent_steps",
    "approvals",
    "baselines",
    "diagnoses",
    "evidence",
    "faults",
    "incident_events",
    "incidents",
    "memories",
    "reports",
    "settings",
    "suspects",
    "verifications",
}


def test_every_plan_table_is_declared() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_evidence_codes_are_unique_per_incident() -> None:
    table = Base.metadata.tables["evidence"]
    unique_sets = {
        tuple(sorted(col.name for col in constraint.columns))
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("code", "incident_id") in unique_sets


def test_memories_embedding_is_1024_dimensional_hnsw() -> None:
    table = Base.metadata.tables["memories"]
    embedding = table.c.embedding
    assert isinstance(embedding.type, Vector)
    assert embedding.type.dim == 1024
    index = next(idx for idx in table.indexes if idx.name == "ix_memories_embedding_hnsw")
    assert index.dialect_options["postgresql"]["using"] == "hnsw"
    assert index.dialect_options["postgresql"]["ops"] == {"embedding": "vector_cosine_ops"}


def test_incident_public_id_is_unique() -> None:
    table = Base.metadata.tables["incidents"]
    assert table.c.public_id.unique is True


def test_all_child_tables_cascade_from_parent() -> None:
    expected = {
        "incident_events": "incidents",
        "evidence": "incidents",
        "suspects": "incidents",
        "agent_steps": "agent_runs",
        "diagnoses": "agent_runs",
        "actions": "incidents",
        "approvals": "actions",
        "verifications": "actions",
        "reports": "incidents",
    }
    for table_name, parent in expected.items():
        fks = Base.metadata.tables[table_name].foreign_keys
        targets = {fk.target_fullname for fk in fks}
        assert f"{parent}.id" in targets
        for fk in fks:
            if fk.target_fullname == f"{parent}.id":
                assert fk.ondelete == "CASCADE"
