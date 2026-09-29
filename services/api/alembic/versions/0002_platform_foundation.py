"""Platform foundation: provenance, identity/RBAC, daily prices, watchlists, audit.

Securities uniqueness moves from (ticker, exchange) to a partial index over active
listings so retired tickers stay in history when a symbol is reassigned.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_platform_foundation"
down_revision: str | None = "0001_company_directory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_fetches",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("dataset", sa.String(length=80), nullable=False),
        sa.Column("subject", sa.String(length=64), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=False),
        sa.Column("request_params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("record_count", sa.Integer(), nullable=True),
        sa.Column("rejected_count", sa.Integer(), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("license_note", sa.String(length=300), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_fetches")),
    )
    op.create_index(
        "ix_provider_fetches_lookup",
        "provider_fetches",
        ["provider", "dataset", "subject", "retrieved_at"],
        unique=False,
    )
    op.create_index(
        "ix_provider_fetches_retrieved_at", "provider_fetches", ["retrieved_at"], unique=False
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("failed_login_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role in ('viewer', 'analyst', 'admin')", name=op.f("ck_users_role_valid")
        ),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_lowercase")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("resource_type", sa.String(length=60), nullable=True),
        sa.Column("resource_id", sa.String(length=80), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_events_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        "ix_audit_events_action_time", "audit_events", ["action", "occurred_at"], unique=False
    )
    op.create_index(
        "ix_audit_events_actor_time", "audit_events", ["actor_user_id", "occurred_at"], unique=False
    )
    op.create_table(
        "user_identities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("issuer", sa.String(length=300), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_identities_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_identities")),
        sa.UniqueConstraint("issuer", "subject", name=op.f("uq_user_identities_issuer_subject")),
    )
    op.create_index(
        op.f("ix_user_identities_user_id"), "user_identities", ["user_id"], unique=False
    )
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token", sa.String(length=64), nullable=False),
        sa.Column("auth_method", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_user_sessions_token_hash")),
    )
    op.create_index(op.f("ix_user_sessions_user_id"), "user_sessions", ["user_id"], unique=False)
    op.create_table(
        "watchlists",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_watchlists_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watchlists")),
        sa.UniqueConstraint("owner_id", "name", name=op.f("uq_watchlists_owner_id_name")),
    )
    op.create_index(op.f("ix_watchlists_owner_id"), "watchlists", ["owner_id"], unique=False)
    op.create_table(
        "daily_prices",
        sa.Column("security_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("high", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("low", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("close", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("adj_open", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("adj_high", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("adj_low", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("adj_close", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("adj_volume", sa.BigInteger(), nullable=True),
        sa.Column("dividend_cash", sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column("split_factor", sa.Numeric(precision=20, scale=10), nullable=True),
        sa.Column("fetch_id", sa.BigInteger(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["fetch_id"],
            ["provider_fetches.id"],
            name=op.f("fk_daily_prices_fetch_id_provider_fetches"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.id"],
            name=op.f("fk_daily_prices_security_id_securities"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "security_id", "provider", "trade_date", name=op.f("pk_daily_prices")
        ),
    )
    op.create_index(op.f("ix_daily_prices_fetch_id"), "daily_prices", ["fetch_id"], unique=False)
    op.create_table(
        "watchlist_items",
        sa.Column("watchlist_id", sa.Uuid(), nullable=False),
        sa.Column("security_id", sa.Integer(), nullable=False),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.id"],
            name=op.f("fk_watchlist_items_security_id_securities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["watchlist_id"],
            ["watchlists.id"],
            name=op.f("fk_watchlist_items_watchlist_id_watchlists"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("watchlist_id", "security_id", name=op.f("pk_watchlist_items")),
    )
    op.add_column("companies", sa.Column("cik", sa.Integer(), nullable=True))
    op.add_column(
        "companies", sa.Column("classification_system", sa.String(length=20), nullable=True)
    )
    op.add_column("companies", sa.Column("sic_code", sa.String(length=4), nullable=True))
    op.add_column("companies", sa.Column("entity_type", sa.String(length=40), nullable=True))
    op.add_column("companies", sa.Column("filer_category", sa.String(length=80), nullable=True))
    op.add_column(
        "companies", sa.Column("state_of_incorporation", sa.String(length=80), nullable=True)
    )
    op.add_column("companies", sa.Column("fiscal_year_end", sa.String(length=4), nullable=True))
    op.add_column("companies", sa.Column("website", sa.String(length=300), nullable=True))
    op.add_column("companies", sa.Column("hq_city", sa.String(length=120), nullable=True))
    op.add_column("companies", sa.Column("hq_region", sa.String(length=120), nullable=True))
    op.add_column(
        "companies",
        sa.Column(
            "former_names",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("companies", sa.Column("directory_fetch_id", sa.BigInteger(), nullable=True))
    op.add_column("companies", sa.Column("profile_fetch_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "companies", sa.Column("profile_refreshed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "companies",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "companies",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint(op.f("uq_companies_cik"), "companies", ["cik"])
    op.create_foreign_key(
        op.f("fk_companies_profile_fetch_id_provider_fetches"),
        "companies",
        "provider_fetches",
        ["profile_fetch_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_companies_directory_fetch_id_provider_fetches"),
        "companies",
        "provider_fetches",
        ["directory_fetch_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "securities",
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column(
        "securities", sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "securities", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("securities", sa.Column("source_fetch_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "securities",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "securities",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.drop_index(op.f("ix_securities_ticker_exchange"), table_name="securities")
    op.drop_constraint(op.f("uq_securities_ticker_exchange"), "securities", type_="unique")
    op.create_index("ix_securities_ticker", "securities", ["ticker"], unique=False)
    op.create_index(
        "uq_securities_active_ticker_exchange",
        "securities",
        ["ticker", "exchange"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        postgresql_nulls_not_distinct=True,
    )
    op.create_foreign_key(
        op.f("fk_securities_source_fetch_id_provider_fetches"),
        "securities",
        "provider_fetches",
        ["source_fetch_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_securities_source_fetch_id_provider_fetches"), "securities", type_="foreignkey"
    )
    op.drop_index(
        "uq_securities_active_ticker_exchange",
        table_name="securities",
        postgresql_where=sa.text("is_active"),
        postgresql_nulls_not_distinct=True,
    )
    op.drop_index("ix_securities_ticker", table_name="securities")
    op.create_unique_constraint(
        op.f("uq_securities_ticker_exchange"),
        "securities",
        ["ticker", "exchange"],
        postgresql_nulls_not_distinct=False,
    )
    op.create_index(
        op.f("ix_securities_ticker_exchange"), "securities", ["ticker", "exchange"], unique=False
    )
    op.drop_column("securities", "updated_at")
    op.drop_column("securities", "created_at")
    op.drop_column("securities", "source_fetch_id")
    op.drop_column("securities", "last_seen_at")
    op.drop_column("securities", "first_seen_at")
    op.drop_column("securities", "is_active")
    op.drop_constraint(
        op.f("fk_companies_directory_fetch_id_provider_fetches"), "companies", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_companies_profile_fetch_id_provider_fetches"), "companies", type_="foreignkey"
    )
    op.drop_constraint(op.f("uq_companies_cik"), "companies", type_="unique")
    op.drop_column("companies", "updated_at")
    op.drop_column("companies", "created_at")
    op.drop_column("companies", "profile_refreshed_at")
    op.drop_column("companies", "profile_fetch_id")
    op.drop_column("companies", "directory_fetch_id")
    op.drop_column("companies", "former_names")
    op.drop_column("companies", "hq_region")
    op.drop_column("companies", "hq_city")
    op.drop_column("companies", "website")
    op.drop_column("companies", "fiscal_year_end")
    op.drop_column("companies", "state_of_incorporation")
    op.drop_column("companies", "filer_category")
    op.drop_column("companies", "entity_type")
    op.drop_column("companies", "sic_code")
    op.drop_column("companies", "classification_system")
    op.drop_column("companies", "cik")
    op.drop_table("watchlist_items")
    op.drop_index(op.f("ix_daily_prices_fetch_id"), table_name="daily_prices")
    op.drop_table("daily_prices")
    op.drop_index(op.f("ix_watchlists_owner_id"), table_name="watchlists")
    op.drop_table("watchlists")
    op.drop_index(op.f("ix_user_sessions_user_id"), table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index(op.f("ix_user_identities_user_id"), table_name="user_identities")
    op.drop_table("user_identities")
    op.drop_index("ix_audit_events_actor_time", table_name="audit_events")
    op.drop_index("ix_audit_events_action_time", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("users")
    op.drop_index("ix_provider_fetches_retrieved_at", table_name="provider_fetches")
    op.drop_index("ix_provider_fetches_lookup", table_name="provider_fetches")
    op.drop_table("provider_fetches")
