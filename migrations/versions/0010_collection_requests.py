"""Automatic UI-context collection and durable first-run requests."""

import json
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0010_collection_requests"
down_revision = "0009_context_building_floors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("searches", sa.Column("collection_requested_at", sa.DateTime(timezone=True)))
    op.add_column(
        "searches",
        sa.Column("auto_collect", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    connection = op.get_bind()
    contexts = sa.table(
        "search_contexts",
        sa.column("id", sa.Uuid()),
        sa.column("rules", sa.JSON()),
        sa.column("enabled", sa.Boolean()),
    )
    searches = sa.table(
        "searches",
        sa.column("context_id", sa.Uuid()),
        sa.column("auto_collect", sa.Boolean()),
        sa.column("collection_requested_at", sa.DateTime(timezone=True)),
    )
    for context_id, rules in connection.execute(
        sa.select(contexts.c.id, contexts.c.rules).where(contexts.c.enabled.is_(True))
    ):
        if isinstance(rules, str):
            rules = json.loads(rules)
        if isinstance(rules, dict) and rules.get("created_from_ui"):
            connection.execute(
                searches.update()
                .where(searches.c.context_id == context_id)
                .values(auto_collect=True, collection_requested_at=datetime.now(UTC))
            )


def downgrade() -> None:
    with op.batch_alter_table("searches") as batch:
        batch.drop_column("collection_requested_at")
        batch.drop_column("auto_collect")
