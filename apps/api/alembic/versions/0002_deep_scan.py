"""Faz 2: derin tarama kolonları, scan_events, scan_assets, reference_sites

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

verification = postgresql.ENUM(
    "confirmed", "refuted", "escalated", "not_verifiable", name="verification", create_type=False
)
reference_site_status = postgresql.ENUM(
    "active",
    "failed",
    "robots_blocked",
    "no_field_data",
    name="reference_site_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    for e in (verification, reference_site_status):
        e.create(bind, checkfirst=True)

    op.add_column(
        "scans",
        sa.Column(
            "parent_scan_id",
            sa.Uuid(),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "scans",
        sa.Column(
            "compare_to_id",
            sa.Uuid(),
            sa.ForeignKey("scans.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("scans", sa.Column("progress", postgresql.JSONB(), nullable=True))
    op.add_column("scans", sa.Column("options", postgresql.JSONB(), nullable=True))
    op.add_column("scans", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_scans_parent_scan_id", "scans", ["parent_scan_id"])

    op.add_column(
        "findings", sa.Column("source_phase", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column("findings", sa.Column("verification", verification, nullable=True))
    op.add_column("findings", sa.Column("module", sa.Text(), nullable=True))

    op.create_table(
        "scan_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "scan_id", sa.Uuid(), sa.ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("module", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_scan_events_scan_id", "scan_events", ["scan_id"])

    op.create_table(
        "scan_assets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "scan_id", sa.Uuid(), sa.ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("page_url", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_scan_assets_scan_id", "scan_assets", ["scan_id"])

    op.create_table(
        "reference_sites",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("status", reference_site_status, nullable=False),
        sa.Column("popularity", postgresql.JSONB(), nullable=True),
        sa.Column("anatomy", postgresql.JSONB(), nullable=True),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.UniqueConstraint("category", "origin", name="uq_reference_category_origin"),
    )
    op.create_index("ix_reference_sites_category", "reference_sites", ["category"])


def downgrade() -> None:
    op.drop_table("reference_sites")
    op.drop_table("scan_assets")
    op.drop_table("scan_events")
    op.drop_column("findings", "module")
    op.drop_column("findings", "verification")
    op.drop_column("findings", "source_phase")
    op.drop_index("ix_scans_parent_scan_id", table_name="scans")
    for column in ("started_at", "options", "progress", "compare_to_id", "parent_scan_id"):
        op.drop_column("scans", column)
    bind = op.get_bind()
    for e in (reference_site_status, verification):
        e.drop(bind, checkfirst=True)
