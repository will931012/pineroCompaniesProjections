"""Fundamentals: point-in-time XBRL facts and latest company metrics.

`financial_facts` keeps each distinct reported value with the date it became public, so
statements can be rebuilt as known on any past date. `company_metrics` holds the latest
screenable value per metric.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_fundamentals"
down_revision: str | None = "0002_platform_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_metrics",
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("metric", sa.String(length=60), nullable=False),
        sa.Column("value", sa.Numeric(precision=28, scale=10), nullable=False),
        sa.Column("basis", sa.String(length=160), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("available_date", sa.Date(), nullable=True),
        sa.Column(
            "derived_from_price", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("formula_version", sa.String(length=20), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_company_metrics_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("company_id", "metric", name=op.f("pk_company_metrics")),
    )
    op.create_index(
        "ix_company_metrics_metric_value", "company_metrics", ["metric", "value"], unique=False
    )
    op.create_table(
        "financial_facts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("taxonomy", sa.String(length=20), nullable=False),
        sa.Column("concept", sa.String(length=200), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(precision=28, scale=6), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column("fiscal_period", sa.String(length=4), nullable=True),
        sa.Column("form", sa.String(length=12), nullable=False),
        sa.Column("accession", sa.String(length=25), nullable=False),
        sa.Column("filed_date", sa.Date(), nullable=False),
        sa.Column("fetch_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_financial_facts_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["fetch_id"],
            ["provider_fetches.id"],
            name=op.f("fk_financial_facts_fetch_id_provider_fetches"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_financial_facts")),
    )
    op.create_index(
        "ix_financial_facts_company_filed",
        "financial_facts",
        ["company_id", "filed_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_financial_facts_fetch_id"), "financial_facts", ["fetch_id"], unique=False
    )
    op.create_index(
        "uq_financial_facts_observation",
        "financial_facts",
        ["company_id", "taxonomy", "concept", "unit", "period_start", "period_end", "value"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.add_column("companies", sa.Column("fundamentals_fetch_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "companies",
        sa.Column("fundamentals_refreshed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_companies_fundamentals_fetch_id_provider_fetches"),
        "companies",
        "provider_fetches",
        ["fundamentals_fetch_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_companies_fundamentals_fetch_id_provider_fetches"), "companies", type_="foreignkey"
    )
    op.drop_column("companies", "fundamentals_refreshed_at")
    op.drop_column("companies", "fundamentals_fetch_id")
    op.drop_index(
        "uq_financial_facts_observation",
        table_name="financial_facts",
        postgresql_nulls_not_distinct=True,
    )
    op.drop_index(op.f("ix_financial_facts_fetch_id"), table_name="financial_facts")
    op.drop_index("ix_financial_facts_company_filed", table_name="financial_facts")
    op.drop_table("financial_facts")
    op.drop_index("ix_company_metrics_metric_value", table_name="company_metrics")
    op.drop_table("company_metrics")
