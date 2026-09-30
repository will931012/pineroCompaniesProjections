"""SEC filings: filing index, extracted sections, search chunks, insider transactions.

`filing_chunks` carries a generated English tsvector (full-text search) and a 384-dimension
pgvector embedding with an HNSW cosine index. The `vector` extension is created here;
PostgreSQL must have pgvector installed (Railway's image does).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_filings"
down_revision: str | None = "0003_fundamentals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "filings",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("accession", sa.String(length=20), nullable=False),
        sa.Column("form", sa.String(length=20), nullable=False),
        sa.Column("filed_date", sa.Date(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("report_date", sa.Date(), nullable=True),
        sa.Column("primary_document", sa.String(length=300), nullable=True),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column("items", sa.String(length=120), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("is_xbrl", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_inline_xbrl", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("index_fetch_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "document_status",
            sa.String(length=20),
            server_default=sa.text("'not_loaded'"),
            nullable=False,
        ),
        sa.Column("document_error", sa.String(length=300), nullable=True),
        sa.Column("document_fetch_id", sa.BigInteger(), nullable=True),
        sa.Column("extractor_version", sa.String(length=20), nullable=True),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_filings_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_fetch_id"],
            ["provider_fetches.id"],
            name=op.f("fk_filings_document_fetch_id_provider_fetches"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["index_fetch_id"],
            ["provider_fetches.id"],
            name=op.f("fk_filings_index_fetch_id_provider_fetches"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_filings")),
        sa.UniqueConstraint(
            "company_id", "accession", name=op.f("uq_filings_company_id_accession")
        ),
    )
    op.create_index(
        "ix_filings_company_filed", "filings", ["company_id", "filed_date"], unique=False
    )
    op.create_index(
        "ix_filings_company_form_filed",
        "filings",
        ["company_id", "form", "filed_date"],
        unique=False,
    )
    op.create_index(op.f("ix_filings_index_fetch_id"), "filings", ["index_fetch_id"], unique=False)
    op.create_table(
        "filing_sections",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("filing_id", sa.BigInteger(), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("part", sa.String(length=8), nullable=True),
        sa.Column("item", sa.String(length=10), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["filing_id"],
            ["filings.id"],
            name=op.f("fk_filing_sections_filing_id_filings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_filing_sections")),
        sa.UniqueConstraint("filing_id", "key", name=op.f("uq_filing_sections_filing_id_key")),
    )
    op.create_index(
        op.f("ix_filing_sections_filing_id"), "filing_sections", ["filing_id"], unique=False
    )
    op.create_table(
        "insider_transactions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("filing_id", sa.BigInteger(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("owners", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("owner_cik", sa.BigInteger(), nullable=True),
        sa.Column("owner_name", sa.String(length=200), nullable=False),
        sa.Column("is_director", sa.Boolean(), nullable=False),
        sa.Column("is_officer", sa.Boolean(), nullable=False),
        sa.Column("is_ten_percent_owner", sa.Boolean(), nullable=False),
        sa.Column("officer_title", sa.String(length=200), nullable=True),
        sa.Column("is_derivative", sa.Boolean(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("security_title", sa.String(length=200), nullable=True),
        sa.Column("transaction_date", sa.Date(), nullable=True),
        sa.Column("transaction_code", sa.String(length=4), nullable=True),
        sa.Column("shares", sa.Numeric(precision=28, scale=6), nullable=True),
        sa.Column("price", sa.Numeric(precision=28, scale=6), nullable=True),
        sa.Column("acquired_disposed", sa.String(length=1), nullable=True),
        sa.Column("shares_owned_after", sa.Numeric(precision=28, scale=6), nullable=True),
        sa.Column("ownership", sa.String(length=1), nullable=True),
        sa.Column("ownership_nature", sa.String(length=200), nullable=True),
        sa.Column("rule_10b5_1", sa.Boolean(), nullable=True),
        sa.Column("underlying_security", sa.String(length=200), nullable=True),
        sa.Column("exercise_price", sa.Numeric(precision=28, scale=6), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_insider_transactions_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["filing_id"],
            ["filings.id"],
            name=op.f("fk_insider_transactions_filing_id_filings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_insider_transactions")),
        sa.UniqueConstraint(
            "filing_id",
            "is_derivative",
            "ordinal",
            name=op.f("uq_insider_transactions_filing_id_is_derivative_ordinal"),
        ),
    )
    op.create_index(
        "ix_insider_transactions_company_date",
        "insider_transactions",
        ["company_id", "transaction_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_insider_transactions_filing_id"),
        "insider_transactions",
        ["filing_id"],
        unique=False,
    )
    op.create_table(
        "filing_chunks",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("section_id", sa.BigInteger(), nullable=False),
        sa.Column("filing_id", sa.BigInteger(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column(
            "search",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', text)", persisted=True),
            nullable=False,
        ),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column("embedding_model", sa.String(length=80), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_filing_chunks_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["filing_id"],
            ["filings.id"],
            name=op.f("fk_filing_chunks_filing_id_filings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["section_id"],
            ["filing_sections.id"],
            name=op.f("fk_filing_chunks_section_id_filing_sections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_filing_chunks")),
        sa.UniqueConstraint(
            "section_id", "ordinal", name=op.f("uq_filing_chunks_section_id_ordinal")
        ),
    )
    op.create_index(
        op.f("ix_filing_chunks_company_id"), "filing_chunks", ["company_id"], unique=False
    )
    op.create_index(
        "ix_filing_chunks_embedding",
        "filing_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        op.f("ix_filing_chunks_filing_id"), "filing_chunks", ["filing_id"], unique=False
    )
    op.create_index(
        "ix_filing_chunks_search", "filing_chunks", ["search"], unique=False, postgresql_using="gin"
    )
    op.create_index(
        op.f("ix_filing_chunks_section_id"), "filing_chunks", ["section_id"], unique=False
    )
    op.add_column(
        "companies", sa.Column("filings_refreshed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "companies",
        sa.Column(
            "filings_history_loaded", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("companies", "filings_history_loaded")
    op.drop_column("companies", "filings_refreshed_at")
    op.drop_index(op.f("ix_filing_chunks_section_id"), table_name="filing_chunks")
    op.drop_index("ix_filing_chunks_search", table_name="filing_chunks", postgresql_using="gin")
    op.drop_index(op.f("ix_filing_chunks_filing_id"), table_name="filing_chunks")
    op.drop_index(
        "ix_filing_chunks_embedding",
        table_name="filing_chunks",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_index(op.f("ix_filing_chunks_company_id"), table_name="filing_chunks")
    op.drop_table("filing_chunks")
    op.drop_index(op.f("ix_insider_transactions_filing_id"), table_name="insider_transactions")
    op.drop_index("ix_insider_transactions_company_date", table_name="insider_transactions")
    op.drop_table("insider_transactions")
    op.drop_index(op.f("ix_filing_sections_filing_id"), table_name="filing_sections")
    op.drop_table("filing_sections")
    op.drop_index(op.f("ix_filings_index_fetch_id"), table_name="filings")
    op.drop_index("ix_filings_company_form_filed", table_name="filings")
    op.drop_index("ix_filings_company_filed", table_name="filings")
    op.drop_table("filings")
