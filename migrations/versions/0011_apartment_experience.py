"""Apartment AI jobs, geographic evidence cache and initial discovery."""

import sqlalchemy as sa
from alembic import op

revision = "0011_apartment_experience"
down_revision = "0010_collection_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "searches",
        sa.Column("discovery_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        sa.text(
            "UPDATE searches SET discovery_pending=1 WHERE auto_collect=1 AND enabled=1 AND id IN (SELECT search_id FROM collector_runs GROUP BY search_id HAVING max(pages_processed)=1)"
        )
    )
    op.create_table(
        "ai_review_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
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
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_ai_review_jobs_user_id", "ai_review_jobs", ["user_id"])
    op.create_table(
        "location_evidence",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("location_evidence")
    op.drop_table("ai_review_jobs")
    with op.batch_alter_table("searches") as batch:
        batch.drop_column("discovery_pending")
