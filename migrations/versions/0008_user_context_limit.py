"""Manual subscription context capacity."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_user_context_limit"
down_revision = "0007_context_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("context_limit", sa.Integer(), nullable=False, server_default="1")
    )
    connection = op.get_bind()
    users = sa.table("users", sa.column("id", sa.Uuid()), sa.column("context_limit", sa.Integer()))
    contexts = sa.table(
        "search_contexts", sa.column("owner_user_id", sa.Uuid()), sa.column("enabled", sa.Boolean())
    )
    counts = connection.execute(
        sa.select(contexts.c.owner_user_id, sa.func.count())
        .where(contexts.c.enabled.is_(True), contexts.c.owner_user_id.is_not(None))
        .group_by(contexts.c.owner_user_id)
    ).all()
    for user_id, count in counts:
        if count > 1:
            connection.execute(
                users.update().where(users.c.id == user_id).values(context_limit=count)
            )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("context_limit")
