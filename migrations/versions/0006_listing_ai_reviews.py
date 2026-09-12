"""Listing AI reviews."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_listing_ai_reviews"
down_revision = "0005_auth_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "listing_ai_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "context_id",
            sa.Uuid(),
            sa.ForeignKey("search_contexts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "listing_id",
            sa.Uuid(),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "group_id",
            sa.Uuid(),
            sa.ForeignKey("apartment_groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("model_name", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("ai_score", sa.Integer(), nullable=True),
        sa.Column("verdict", sa.String(50), nullable=True),
        sa.Column("pros", sa.JSON(), nullable=True),
        sa.Column("cons", sa.JSON(), nullable=True),
        sa.Column("risks", sa.JSON(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("raw_response", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "user_id",
            "context_id",
            "listing_id",
            "model_name",
            "prompt_version",
            "input_hash",
            name="uq_listing_ai_review_input",
        ),
    )
    op.create_index(op.f("ix_listing_ai_reviews_user_id"), "listing_ai_reviews", ["user_id"])
    op.create_index(op.f("ix_listing_ai_reviews_context_id"), "listing_ai_reviews", ["context_id"])
    op.create_index(op.f("ix_listing_ai_reviews_listing_id"), "listing_ai_reviews", ["listing_id"])
    op.create_index(op.f("ix_listing_ai_reviews_group_id"), "listing_ai_reviews", ["group_id"])
    op.create_index(op.f("ix_listing_ai_reviews_model_name"), "listing_ai_reviews", ["model_name"])
    op.create_index(
        op.f("ix_listing_ai_reviews_prompt_version"),
        "listing_ai_reviews",
        ["prompt_version"],
    )
    op.create_index(op.f("ix_listing_ai_reviews_input_hash"), "listing_ai_reviews", ["input_hash"])


def downgrade() -> None:
    op.drop_index(op.f("ix_listing_ai_reviews_input_hash"), table_name="listing_ai_reviews")
    op.drop_index(op.f("ix_listing_ai_reviews_prompt_version"), table_name="listing_ai_reviews")
    op.drop_index(op.f("ix_listing_ai_reviews_model_name"), table_name="listing_ai_reviews")
    op.drop_index(op.f("ix_listing_ai_reviews_group_id"), table_name="listing_ai_reviews")
    op.drop_index(op.f("ix_listing_ai_reviews_listing_id"), table_name="listing_ai_reviews")
    op.drop_index(op.f("ix_listing_ai_reviews_context_id"), table_name="listing_ai_reviews")
    op.drop_index(op.f("ix_listing_ai_reviews_user_id"), table_name="listing_ai_reviews")
    op.drop_table("listing_ai_reviews")
