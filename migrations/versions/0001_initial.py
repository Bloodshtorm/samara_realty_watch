"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-27
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    ]


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    op.create_table(
        "searches",
        uuid_pk(),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("source", sa.String(50), nullable=False, index=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("rooms", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("interval_hours", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("max_pages", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("last_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_status", sa.String(50)),
        sa.Column("last_error", sa.Text()),
        *timestamps(),
    )
    op.create_table(
        "listings",
        uuid_pk(),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("source_listing_id", sa.String(300), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_active_at", sa.DateTime(timezone=True)),
        sa.Column("inactive_at", sa.DateTime(timezone=True)),
        sa.Column("title", sa.Text()),
        sa.Column("address_raw", sa.Text()),
        sa.Column("address_normalized", sa.Text()),
        sa.Column("district", sa.String(100)),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        sa.Column("property_type", sa.String(50)),
        sa.Column("seller_type", sa.String(50)),
        sa.Column("rooms", sa.Integer()),
        sa.Column("area_total_m2", sa.Numeric(10, 2)),
        sa.Column("area_living_m2", sa.Numeric(10, 2)),
        sa.Column("area_kitchen_m2", sa.Numeric(10, 2)),
        sa.Column("price_rub", sa.Integer()),
        sa.Column("price_per_m2", sa.Integer()),
        sa.Column("floor", sa.Integer()),
        sa.Column("floors_total", sa.Integer()),
        sa.Column("building_year", sa.Integer()),
        sa.Column("building_type", sa.String(100)),
        sa.Column("description", sa.Text()),
        sa.Column("phone_masked", sa.String(100)),
        sa.Column("photos_count", sa.Integer()),
        sa.Column("raw_payload", postgresql.JSONB()),
        sa.Column("features", postgresql.JSONB()),
        sa.Column("score", sa.Integer()),
        sa.Column("score_details", postgresql.JSONB()),
        sa.Column("score_reasons", postgresql.JSONB()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *timestamps(),
        sa.UniqueConstraint("source", "source_listing_id", name="uq_listing_source_id"),
    )
    op.create_table(
        "listing_observations",
        uuid_pk(),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("listings.id", ondelete="CASCADE")),
        sa.Column("search_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("searches.id", ondelete="CASCADE")),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("price_rub", sa.Integer()),
        sa.Column("price_per_m2", sa.Integer()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("title_snapshot", sa.Text()),
        sa.Column("description_snapshot", sa.Text()),
        sa.Column("raw_payload", postgresql.JSONB()),
    )
    op.create_table(
        "price_history",
        uuid_pk(),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("listings.id", ondelete="CASCADE")),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("old_price_rub", sa.Integer()),
        sa.Column("new_price_rub", sa.Integer(), nullable=False),
        sa.Column("change_rub", sa.Integer()),
        sa.Column("change_percent", sa.Float()),
    )
    op.create_table(
        "listing_links",
        uuid_pk(),
        sa.Column("listing_id_a", postgresql.UUID(as_uuid=True), sa.ForeignKey("listings.id", ondelete="CASCADE")),
        sa.Column("listing_id_b", postgresql.UUID(as_uuid=True), sa.ForeignKey("listings.id", ondelete="CASCADE")),
        sa.Column("match_type", sa.String(50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("match_reason", postgresql.JSONB(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("listing_id_a", "listing_id_b", "match_type"),
    )
    op.create_table(
        "collector_runs",
        uuid_pk(),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(50), nullable=False, server_default="started"),
        sa.Column("source", sa.String(50)),
        sa.Column("search_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("searches.id", ondelete="SET NULL")),
        sa.Column("pages_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("listings_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("listings_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("listings_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("price_changes_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text()),
        sa.Column("debug_screenshot_path", sa.Text()),
        sa.Column("debug_html_path", sa.Text()),
    )
    op.create_table(
        "notifications",
        uuid_pk(),
        sa.Column("listing_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("listings.id", ondelete="CASCADE")),
        sa.Column("notification_type", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(300), nullable=False),
        sa.Column("payload", postgresql.JSONB()),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("delivery_status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text()),
        sa.UniqueConstraint("idempotency_key", name="uq_notification_idempotency_key"),
    )


def downgrade() -> None:
    for table in (
        "notifications",
        "collector_runs",
        "listing_links",
        "price_history",
        "listing_observations",
        "listings",
        "searches",
    ):
        op.drop_table(table)
