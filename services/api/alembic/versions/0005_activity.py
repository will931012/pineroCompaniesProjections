"""Background jobs, news items and company mentions, events, alert rules, and alerts.

`jobs` is a PostgreSQL work queue: workers claim rows with FOR UPDATE SKIP LOCKED, and a
partial unique index allows one queued/running job per dedupe key. `news_items` keeps only
article metadata (title, outlet, link), never article bodies.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_activity"
down_revision: str | None = "0004_filings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_by", sa.String(length=80), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("dedupe_key", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index("ix_jobs_status_run_at", "jobs", ["status", "run_at"], unique=False)
    op.create_index(
        "uq_jobs_dedupe_active",
        "jobs",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_table(
        "news_items",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("url_sha256", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("domain", sa.String(length=200), nullable=True),
        sa.Column("language", sa.String(length=40), nullable=True),
        sa.Column("source_country", sa.String(length=80), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetch_id", sa.BigInteger(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column("embedding_model", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["fetch_id"],
            ["provider_fetches.id"],
            name=op.f("fk_news_items_fetch_id_provider_fetches"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_news_items")),
        sa.UniqueConstraint(
            "provider", "url_sha256", name=op.f("uq_news_items_provider_url_sha256")
        ),
    )
    op.create_index(op.f("ix_news_items_fetch_id"), "news_items", ["fetch_id"], unique=False)
    op.create_index(op.f("ix_news_items_seen_at"), "news_items", ["seen_at"], unique=False)
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tickers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("watchlist_id", sa.Uuid(), nullable=True),
        sa.Column("email", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_alert_rules_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["watchlist_id"],
            ["watchlists.id"],
            name=op.f("fk_alert_rules_watchlist_id_watchlists"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_rules")),
    )
    op.create_index(op.f("ix_alert_rules_owner_id"), "alert_rules", ["owner_id"], unique=False)
    op.create_table(
        "news_mentions",
        sa.Column("news_id", sa.BigInteger(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.String(length=8), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_news_mentions_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["news_id"],
            ["news_items.id"],
            name=op.f("fk_news_mentions_news_id_news_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("news_id", "company_id", name=op.f("pk_news_mentions")),
    )
    op.create_index(
        op.f("ix_news_mentions_company_id"), "news_mentions", ["company_id"], unique=False
    )
    op.create_table(
        "alerts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("subject_key", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("email_status", sa.String(length=16), nullable=False),
        sa.Column("email_error", sa.String(length=300), nullable=True),
        sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_alerts_company_id_companies"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_alerts_owner_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["alert_rules.id"],
            name=op.f("fk_alerts_rule_id_alert_rules"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
        sa.UniqueConstraint("rule_id", "subject_key", name=op.f("uq_alerts_rule_id_subject_key")),
    )
    op.create_index(op.f("ix_alerts_owner_id"), "alerts", ["owner_id"], unique=False)
    op.create_index(op.f("ix_alerts_rule_id"), "alerts", ["rule_id"], unique=False)
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_kind", sa.String(length=12), nullable=False),
        sa.Column("filing_id", sa.BigInteger(), nullable=True),
        sa.Column("news_id", sa.BigInteger(), nullable=True),
        sa.Column("cluster_id", sa.BigInteger(), nullable=True),
        sa.Column("novelty", sa.String(length=8), nullable=False),
        sa.Column("prior_similarity", sa.Float(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("classifier_version", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_events_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["filing_id"],
            ["filings.id"],
            name=op.f("fk_events_filing_id_filings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["news_id"],
            ["news_items.id"],
            name=op.f("fk_events_news_id_news_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )
    op.create_index(op.f("ix_events_cluster_id"), "events", ["cluster_id"], unique=False)
    op.create_index(
        "ix_events_company_occurred", "events", ["company_id", "occurred_at"], unique=False
    )
    op.create_index(
        "uq_events_company_filing",
        "events",
        ["company_id", "filing_id"],
        unique=True,
        postgresql_where=sa.text("filing_id IS NOT NULL"),
    )
    op.create_index(
        "uq_events_company_news",
        "events",
        ["company_id", "news_id"],
        unique=True,
        postgresql_where=sa.text("news_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_events_company_news",
        table_name="events",
        postgresql_where=sa.text("news_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_events_company_filing",
        table_name="events",
        postgresql_where=sa.text("filing_id IS NOT NULL"),
    )
    op.drop_index("ix_events_company_occurred", table_name="events")
    op.drop_index(op.f("ix_events_cluster_id"), table_name="events")
    op.drop_table("events")
    op.drop_index(op.f("ix_alerts_rule_id"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_owner_id"), table_name="alerts")
    op.drop_table("alerts")
    op.drop_index(op.f("ix_news_mentions_company_id"), table_name="news_mentions")
    op.drop_table("news_mentions")
    op.drop_index(op.f("ix_alert_rules_owner_id"), table_name="alert_rules")
    op.drop_table("alert_rules")
    op.drop_index(op.f("ix_news_items_seen_at"), table_name="news_items")
    op.drop_index(op.f("ix_news_items_fetch_id"), table_name="news_items")
    op.drop_table("news_items")
    op.drop_index(
        "uq_jobs_dedupe_active",
        table_name="jobs",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_index("ix_jobs_status_run_at", table_name="jobs")
    op.drop_table("jobs")
