"""Create the initial company and security directory."""

import sqlalchemy as sa

from alembic import op

revision = "0001_company_directory"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("legal_name", sa.String(length=240), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("sector", sa.String(length=120), nullable=True),
        sa.Column("industry", sa.String(length=160), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.create_index("ix_companies_legal_name", "companies", ["legal_name"])
    op.create_table(
        "securities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("exchange", sa.String(length=80), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("ticker", "exchange", name="uq_securities_ticker_exchange"),
    )
    op.create_index("ix_securities_company_id", "securities", ["company_id"])
    op.create_index("ix_securities_ticker_exchange", "securities", ["ticker", "exchange"])


def downgrade() -> None:
    op.drop_index("ix_securities_ticker_exchange", table_name="securities")
    op.drop_index("ix_securities_company_id", table_name="securities")
    op.drop_table("securities")
    op.drop_index("ix_companies_legal_name", table_name="companies")
    op.drop_table("companies")
