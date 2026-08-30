"""search contexts

Revision ID: 0003_search_contexts
Revises: 0002_listing_user_states
Create Date: 2026-08-30
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_search_contexts"
down_revision = "0002_listing_user_states"
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
        "search_contexts",
        uuid_pk(),
        sa.Column("slug", sa.String(120), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("object_type", sa.String(50), nullable=False, server_default="flat"),
        sa.Column("city", sa.String(100), nullable=False, server_default="Самара"),
        sa.Column("expected_rooms", sa.Integer()),
        sa.Column("center_latitude", sa.Float()),
        sa.Column("center_longitude", sa.Float()),
        sa.Column("radius_km", sa.Float()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("rules", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(op.f("ix_search_contexts_slug"), "search_contexts", ["slug"], unique=True)
    op.add_column("searches", sa.Column("context_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_searches_context_id_search_contexts",
        "searches",
        "search_contexts",
        ["context_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_searches_context_id"), "searches", ["context_id"])
    op.alter_column("searches", "rooms", nullable=True)


def downgrade() -> None:
    op.alter_column("searches", "rooms", nullable=False)
    op.drop_index(op.f("ix_searches_context_id"), table_name="searches")
    op.drop_constraint("fk_searches_context_id_search_contexts", "searches", type_="foreignkey")
    op.drop_column("searches", "context_id")
    op.drop_index(op.f("ix_search_contexts_slug"), table_name="search_contexts")
    op.drop_table("search_contexts")
