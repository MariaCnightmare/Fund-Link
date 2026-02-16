"""add frames query columns

Revision ID: 20260216_0002
Revises: 20260216_0001
Create Date: 2026-02-16 00:10:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260216_0002"
down_revision = "20260216_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("network_snapshots", sa.Column("end_date", sa.Date(), nullable=True))
    op.add_column("network_snapshots", sa.Column("window_size", sa.Integer(), nullable=True))
    op.add_column("network_snapshots", sa.Column("job_id", sa.BigInteger(), nullable=True))
    op.create_index(op.f("ix_network_snapshots_end_date"), "network_snapshots", ["end_date"], unique=False)
    op.create_index(op.f("ix_network_snapshots_job_id"), "network_snapshots", ["job_id"], unique=False)
    op.create_index(op.f("ix_network_snapshots_window_size"), "network_snapshots", ["window_size"], unique=False)
    op.create_foreign_key(
        "fk_network_snapshots_job_id_jobs",
        "network_snapshots",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column("network_edges", sa.Column("p_value", sa.Numeric(precision=18, scale=6), nullable=True))
    op.add_column("network_edges", sa.Column("lag", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_network_edges_lag"), "network_edges", ["lag"], unique=False)
    op.create_index(op.f("ix_network_edges_p_value"), "network_edges", ["p_value"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_network_edges_p_value"), table_name="network_edges")
    op.drop_index(op.f("ix_network_edges_lag"), table_name="network_edges")
    op.drop_column("network_edges", "lag")
    op.drop_column("network_edges", "p_value")

    op.drop_constraint("fk_network_snapshots_job_id_jobs", "network_snapshots", type_="foreignkey")
    op.drop_index(op.f("ix_network_snapshots_window_size"), table_name="network_snapshots")
    op.drop_index(op.f("ix_network_snapshots_job_id"), table_name="network_snapshots")
    op.drop_index(op.f("ix_network_snapshots_end_date"), table_name="network_snapshots")
    op.drop_column("network_snapshots", "job_id")
    op.drop_column("network_snapshots", "window_size")
    op.drop_column("network_snapshots", "end_date")
