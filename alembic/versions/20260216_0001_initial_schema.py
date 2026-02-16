"""initial schema

Revision ID: 20260216_0001
Revises:
Create Date: 2026-02-16 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260216_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_jobs_job_type"), "jobs", ["job_type"], unique=False)
    op.create_index(op.f("ix_jobs_status"), "jobs", ["status"], unique=False)

    op.create_table(
        "network_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=True),
        sa.Column("window_end", sa.Date(), nullable=True),
        sa.Column("method", sa.String(length=64), nullable=False),
        sa.Column("node_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("edge_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("as_of_date", "method", name="uq_network_snapshots_date_method"),
    )
    op.create_index(op.f("ix_network_snapshots_as_of_date"), "network_snapshots", ["as_of_date"], unique=False)

    op.create_table(
        "symbols",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("exchange", sa.String(length=64), nullable=True),
        sa.Column("sector", sa.String(length=128), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_symbols_ticker"), "symbols", ["ticker"], unique=True)

    op.create_table(
        "features_daily",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("feature_set_version", sa.String(length=64), server_default=sa.text("'v1'"), nullable=False),
        sa.Column("feature_values", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol_id", "date", "feature_set_version", name="uq_features_daily_symbol_date_version"),
    )
    op.create_index(op.f("ix_features_daily_date"), "features_daily", ["date"], unique=False)
    op.create_index(op.f("ix_features_daily_symbol_id"), "features_daily", ["symbol_id"], unique=False)

    op.create_table(
        "forecasts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol_id", sa.BigInteger(), nullable=False),
        sa.Column("model_name", sa.String(length=64), nullable=False),
        sa.Column("forecast_date", sa.Date(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("prediction", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("lower_bound", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("upper_bound", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("metrics_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol_id", "model_name", "forecast_date", "target_date", name="uq_forecasts_identity"),
    )
    op.create_index(op.f("ix_forecasts_forecast_date"), "forecasts", ["forecast_date"], unique=False)
    op.create_index(op.f("ix_forecasts_model_name"), "forecasts", ["model_name"], unique=False)
    op.create_index(op.f("ix_forecasts_symbol_id"), "forecasts", ["symbol_id"], unique=False)
    op.create_index(op.f("ix_forecasts_target_date"), "forecasts", ["target_date"], unique=False)

    op.create_table(
        "network_edges",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("source_symbol_id", sa.BigInteger(), nullable=False),
        sa.Column("target_symbol_id", sa.BigInteger(), nullable=False),
        sa.Column("weight", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["network_snapshots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_symbol_id"], ["symbols.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_symbol_id"], ["symbols.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id", "source_symbol_id", "target_symbol_id", name="uq_network_edges_snapshot_pair"
        ),
    )
    op.create_index(op.f("ix_network_edges_snapshot_id"), "network_edges", ["snapshot_id"], unique=False)
    op.create_index(op.f("ix_network_edges_source_symbol_id"), "network_edges", ["source_symbol_id"], unique=False)
    op.create_index(op.f("ix_network_edges_target_symbol_id"), "network_edges", ["target_symbol_id"], unique=False)

    op.create_table(
        "prices_daily",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("symbol_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("high", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("low", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("close", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("adj_close", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol_id", "date", name="uq_prices_daily_symbol_date"),
    )
    op.create_index(op.f("ix_prices_daily_date"), "prices_daily", ["date"], unique=False)
    op.create_index(op.f("ix_prices_daily_symbol_id"), "prices_daily", ["symbol_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_prices_daily_symbol_id"), table_name="prices_daily")
    op.drop_index(op.f("ix_prices_daily_date"), table_name="prices_daily")
    op.drop_table("prices_daily")

    op.drop_index(op.f("ix_network_edges_target_symbol_id"), table_name="network_edges")
    op.drop_index(op.f("ix_network_edges_source_symbol_id"), table_name="network_edges")
    op.drop_index(op.f("ix_network_edges_snapshot_id"), table_name="network_edges")
    op.drop_table("network_edges")

    op.drop_index(op.f("ix_forecasts_target_date"), table_name="forecasts")
    op.drop_index(op.f("ix_forecasts_symbol_id"), table_name="forecasts")
    op.drop_index(op.f("ix_forecasts_model_name"), table_name="forecasts")
    op.drop_index(op.f("ix_forecasts_forecast_date"), table_name="forecasts")
    op.drop_table("forecasts")

    op.drop_index(op.f("ix_features_daily_symbol_id"), table_name="features_daily")
    op.drop_index(op.f("ix_features_daily_date"), table_name="features_daily")
    op.drop_table("features_daily")

    op.drop_index(op.f("ix_symbols_ticker"), table_name="symbols")
    op.drop_table("symbols")

    op.drop_index(op.f("ix_network_snapshots_as_of_date"), table_name="network_snapshots")
    op.drop_table("network_snapshots")

    op.drop_index(op.f("ix_jobs_status"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_job_type"), table_name="jobs")
    op.drop_table("jobs")
