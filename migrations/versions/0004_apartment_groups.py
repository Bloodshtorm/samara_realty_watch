"""Apartment groups and durable duplicate decisions."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_apartment_groups"
down_revision = "0003_search_contexts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "apartment_groups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    with op.batch_alter_table("listings") as batch:
        batch.add_column(sa.Column("group_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_listings_group_id", "apartment_groups", ["group_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_listings_group_id", ["group_id"])
    with op.batch_alter_table("listing_links") as batch:
        batch.add_column(
            sa.Column("status", sa.String(20), nullable=False, server_default="candidate")
        )
        batch.add_column(
            sa.Column("decision_origin", sa.String(20), nullable=False, server_default="automatic")
        )
        batch.create_index("ix_listing_links_status", ["status"])


def downgrade() -> None:
    with op.batch_alter_table("listing_links") as batch:
        batch.drop_index("ix_listing_links_status")
        batch.drop_column("decision_origin")
        batch.drop_column("status")
    with op.batch_alter_table("listings") as batch:
        batch.drop_index("ix_listings_group_id")
        batch.drop_constraint("fk_listings_group_id", type_="foreignkey")
        batch.drop_column("group_id")
    op.drop_table("apartment_groups")
