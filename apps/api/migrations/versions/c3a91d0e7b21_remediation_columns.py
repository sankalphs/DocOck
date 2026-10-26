"""remediation columns (P14)

Adds what the approval flow needs beyond the baseline tables: the diagnosis an
action came from, who asked for it, its risk and approval mode, execution
timings, and the single-use token's own expiry and consumption time.

Revision ID: c3a91d0e7b21
Revises: b856170e1f33
Create Date: 2026-10-26 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3a91d0e7b21"
down_revision: str | None = "b856170e1f33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("actions", sa.Column("diagnosis_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_actions_diagnosis_id",
        "actions",
        "diagnoses",
        ["diagnosis_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("actions", sa.Column("requested_by", sa.String(length=64), nullable=True))
    op.add_column("actions", sa.Column("risk", sa.String(length=16), nullable=True))
    op.add_column("actions", sa.Column("approval_mode", sa.String(length=16), nullable=True))
    op.add_column("actions", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("actions", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "approvals", sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "approvals", sa.Column("token_used_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("approvals", "token_used_at")
    op.drop_column("approvals", "token_expires_at")
    op.drop_column("actions", "finished_at")
    op.drop_column("actions", "started_at")
    op.drop_column("actions", "approval_mode")
    op.drop_column("actions", "risk")
    op.drop_column("actions", "requested_by")
    op.drop_constraint("fk_actions_diagnosis_id", "actions", type_="foreignkey")
    op.drop_column("actions", "diagnosis_id")
