"""Store user context settings in typed database columns."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_context_settings"
down_revision = "0006_listing_ai_reviews"
branch_labels = None
depends_on = None

FIELDS = {
    "price_min": sa.Integer(),
    "price_max": sa.Integer(),
    "price_m2_max": sa.Integer(),
    "area_min": sa.Float(),
    "area_max": sa.Float(),
    "floor_min": sa.Integer(),
    "floor_max": sa.Integer(),
    "floors_total_max": sa.Integer(),
    "district": sa.String(200),
    "ai_preferences": sa.Text(),
}


def upgrade() -> None:
    for name, kind in FIELDS.items():
        op.add_column("search_contexts", sa.Column(name, kind, nullable=True))
    table = sa.table(
        "search_contexts",
        sa.column("id", sa.Uuid()),
        sa.column("rules", sa.JSON()),
        *(sa.column(name, kind) for name, kind in FIELDS.items()),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.select(table.c.id, table.c.rules)).mappings().all():
        rules = dict(row["rules"] or {})
        values = {}
        for name, kind in FIELDS.items():
            value = rules.pop(name, None)
            if value not in (None, "") and isinstance(kind, (sa.Integer, sa.Float)):
                value = float(str(value).replace(",", "."))
                if isinstance(kind, sa.Integer):
                    value = int(value)
            values[name] = value if value != "" else None
        connection.execute(
            table.update().where(table.c.id == row["id"]).values(**values, rules=rules)
        )
    op.create_table(
        "deleted_context_slugs",
        sa.Column("slug", sa.String(120), primary_key=True),
        sa.Column(
            "deleted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    table = sa.table(
        "search_contexts",
        sa.column("id", sa.Uuid()),
        sa.column("rules", sa.JSON()),
        *(sa.column(name, kind) for name, kind in FIELDS.items()),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.select(table)).mappings().all():
        rules = {
            **(row["rules"] or {}),
            **{name: row[name] for name in FIELDS if row[name] is not None},
        }
        connection.execute(table.update().where(table.c.id == row["id"]).values(rules=rules))
    op.drop_table("deleted_context_slugs")
    with op.batch_alter_table("search_contexts") as batch:
        for name in reversed(FIELDS):
            batch.drop_column(name)
