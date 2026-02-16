"""add source/currency columns to prices_daily

Revision ID: 20260216_0003
Revises: 20260216_0002
Create Date: 2026-02-16 00:20:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260216_0003"
down_revision = "20260216_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "prices_daily",
        sa.Column("source", sa.String(length=32), nullable=False, server_default=sa.text("'unknown'")),
    )
    op.add_column("prices_daily", sa.Column("currency", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("prices_daily", "currency")
    op.drop_column("prices_daily", "source")
