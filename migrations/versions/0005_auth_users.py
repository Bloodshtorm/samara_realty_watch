"""Auth users and personal contexts."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_auth_users"
down_revision = "0004_apartment_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    is_sqlite = op.get_bind().dialect.name == "sqlite"
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="user"),
        sa.Column("password_hash", sa.String(300), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.create_index(op.f("ix_users_role"), "users", ["role"])
    op.create_index(op.f("ix_users_is_active"), "users", ["is_active"])
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
    )
    op.create_index(op.f("ix_user_sessions_user_id"), "user_sessions", ["user_id"])
    op.create_index(op.f("ix_user_sessions_token_hash"), "user_sessions", ["token_hash"])
    op.create_index(op.f("ix_user_sessions_expires_at"), "user_sessions", ["expires_at"])
    op.create_table(
        "apartment_user_states",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("apartment_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hidden_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "group_id", name="uq_apartment_user_state_user_group"),
    )
    op.create_index(op.f("ix_apartment_user_states_user_id"), "apartment_user_states", ["user_id"])
    op.create_index(op.f("ix_apartment_user_states_group_id"), "apartment_user_states", ["group_id"])
    op.create_index(
        op.f("ix_apartment_user_states_is_favorite"),
        "apartment_user_states",
        ["is_favorite"],
    )
    op.create_index(
        op.f("ix_apartment_user_states_is_hidden"), "apartment_user_states", ["is_hidden"]
    )
    if is_sqlite:
        op.add_column("search_contexts", sa.Column("owner_user_id", sa.Uuid(), nullable=True))
        op.create_index("ix_search_contexts_owner_user_id", "search_contexts", ["owner_user_id"])
    else:
        with op.batch_alter_table("search_contexts") as batch:
            batch.add_column(sa.Column("owner_user_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_search_contexts_owner_user_id_users",
                "users",
                ["owner_user_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch.create_index("ix_search_contexts_owner_user_id", ["owner_user_id"])
    with op.batch_alter_table("listing_user_states") as batch:
        batch.add_column(sa.Column("user_id", sa.Uuid(), nullable=True))
        batch.drop_constraint("uq_listing_user_state_listing_id", type_="unique")
        batch.create_foreign_key(
            "fk_listing_user_states_user_id_users",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_unique_constraint(
            "uq_listing_user_state_user_listing", ["user_id", "listing_id"]
        )
        batch.create_index("ix_listing_user_states_user_id", ["user_id"])


def downgrade() -> None:
    is_sqlite = op.get_bind().dialect.name == "sqlite"
    with op.batch_alter_table("listing_user_states") as batch:
        batch.drop_index("ix_listing_user_states_user_id")
        batch.drop_constraint("uq_listing_user_state_user_listing", type_="unique")
        batch.drop_constraint("fk_listing_user_states_user_id_users", type_="foreignkey")
        batch.create_unique_constraint("uq_listing_user_state_listing_id", ["listing_id"])
        batch.drop_column("user_id")
    if is_sqlite:
        op.drop_index("ix_search_contexts_owner_user_id", table_name="search_contexts")
        op.drop_column("search_contexts", "owner_user_id")
    else:
        with op.batch_alter_table("search_contexts") as batch:
            batch.drop_index("ix_search_contexts_owner_user_id")
            batch.drop_constraint("fk_search_contexts_owner_user_id_users", type_="foreignkey")
            batch.drop_column("owner_user_id")
    op.drop_index(op.f("ix_apartment_user_states_is_hidden"), table_name="apartment_user_states")
    op.drop_index(op.f("ix_apartment_user_states_is_favorite"), table_name="apartment_user_states")
    op.drop_index(op.f("ix_apartment_user_states_group_id"), table_name="apartment_user_states")
    op.drop_index(op.f("ix_apartment_user_states_user_id"), table_name="apartment_user_states")
    op.drop_table("apartment_user_states")
    op.drop_index(op.f("ix_user_sessions_expires_at"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_token_hash"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_user_id"), table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index(op.f("ix_users_is_active"), table_name="users")
    op.drop_index(op.f("ix_users_role"), table_name="users")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
