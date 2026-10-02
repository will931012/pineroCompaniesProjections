"""Market rates (Treasury yields) and saved valuation runs with their assumptions."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_valuation"
down_revision: str | None = "0005_activity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_rates",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("series", sa.String(length=40), nullable=False),
        sa.Column("tenor", sa.String(length=20), nullable=False),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(precision=12, scale=8), nullable=False),
        sa.Column("fetch_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["fetch_id"],
            ["provider_fetches.id"],
            name=op.f("fk_market_rates_fetch_id_provider_fetches"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_rates")),
        sa.UniqueConstraint(
            "series", "tenor", "observed_on", name=op.f("uq_market_rates_series_tenor_observed_on")
        ),
    )
    op.create_index(op.f("ix_market_rates_fetch_id"), "market_rates", ["fetch_id"], unique=False)
    op.create_table(
        "valuation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("model", sa.String(length=10), nullable=False),
        sa.Column("assumptions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("scenarios", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("results", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("price", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("price_date", sa.Date(), nullable=True),
        sa.Column("formula_version", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_valuation_runs_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_valuation_runs_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_valuation_runs")),
    )
    op.create_index(
        "ix_valuation_runs_company_created",
        "valuation_runs",
        ["company_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_valuation_runs_owner_id"), "valuation_runs", ["owner_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_valuation_runs_owner_id"), table_name="valuation_runs")
    op.drop_index("ix_valuation_runs_company_created", table_name="valuation_runs")
    op.drop_table("valuation_runs")
    op.drop_index(op.f("ix_market_rates_fetch_id"), table_name="market_rates")
    op.drop_table("market_rates")
