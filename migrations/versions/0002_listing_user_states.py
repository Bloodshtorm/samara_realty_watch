"""listing user states

Revision ID: 0002_listing_user_states
Revises: 0001_initial
Create Date: 2026-08-30
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_listing_user_states"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def upgrade() -> None:
    op.create_table(
        "listing_user_states",
        uuid_pk(),
        sa.Column(
            "listing_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("hidden_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("listing_id", name="uq_listing_user_state_listing_id"),
    )
    op.create_index(
        op.f("ix_listing_user_states_is_favorite"),
        "listing_user_states",
        ["is_favorite"],
    )
    op.create_index(
        op.f("ix_listing_user_states_is_hidden"),
        "listing_user_states",
        ["is_hidden"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_listing_user_states_is_hidden"), table_name="listing_user_states")
    op.drop_index(op.f("ix_listing_user_states_is_favorite"), table_name="listing_user_states")
    op.drop_table("listing_user_states")
