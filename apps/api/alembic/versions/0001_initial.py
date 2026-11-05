"""initial: scans + findings

Revision ID: 0001
Revises:
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

target_type = postgresql.ENUM("web", "apk", name="target_type", create_type=False)
scan_status = postgresql.ENUM(
    "queued", "running", "done", "failed", name="scan_status", create_type=False
)
severity = postgresql.ENUM(
    "critical", "high", "medium", "low", "info", name="severity", create_type=False
)
category = postgresql.ENUM(
    "security",
    "prod_readiness",
    "performance",
    "conversion",
    "store_compliance",
    name="category",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    for e in (target_type, scan_status, severity, category):
        e.create(bind, checkfirst=True)

    op.create_table(
        "scans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("target_type", target_type, nullable=False),
        sa.Column("target_label", sa.Text(), nullable=False),
        sa.Column("status", scan_status, nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("category_scores", postgresql.JSONB(), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        sa.Column("phase", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_scans_created_at", "scans", ["created_at"])

    op.create_table(
        "findings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "scan_id",
            sa.Uuid(),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("check_id", sa.Text(), nullable=False),
        sa.Column("severity", severity, nullable=False),
        sa.Column("category", category, nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_findings_scan_id", "findings", ["scan_id"])


def downgrade() -> None:
    op.drop_table("findings")
    op.drop_table("scans")
    bind = op.get_bind()
    for e in (category, severity, scan_status, target_type):
        e.drop(bind, checkfirst=True)
