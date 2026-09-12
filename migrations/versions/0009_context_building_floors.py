"""Minimum building height for saved contexts."""

import sqlalchemy as sa
from alembic import op

revision = "0009_context_building_floors"
down_revision = "0008_user_context_limit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("search_contexts", sa.Column("floors_total_min", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("search_contexts") as batch:
        batch.drop_column("floors_total_min")
