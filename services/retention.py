from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import Text, cast, delete, func, null, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CollectorRun, ListingObservation


async def prune_history(
    session: AsyncSession, *, now: datetime | None = None, compact: bool = False
) -> None:
    """Keep 30 days plus boundary observations preserving search membership and first prices."""
    now = now or datetime.now(UTC)
    observation = ListingObservation
    order = (observation.observed_at, observation.id)
    partition = (observation.listing_id, observation.search_id)
    ranked = select(
        observation.id,
        func.row_number().over(partition_by=partition, order_by=order).label("first_rank"),
        func.row_number()
        .over(partition_by=partition, order_by=[column.desc() for column in order])
        .label("last_rank"),
    ).subquery()
    interior_ids = select(ranked.c.id).where(ranked.c.first_rank > 1, ranked.c.last_rank > 1)
    deletion = delete(observation).where(observation.id.in_(interior_ids))
    if not compact:
        deletion = deletion.where(observation.observed_at < now - timedelta(days=30))
    await session.execute(deletion.execution_options(synchronize_session=False))

    # Old large payloads may still exist after upgrading an existing database.
    await session.execute(
        update(observation)
        .where(observation.raw_payload.is_not(None), cast(observation.raw_payload, Text) != "null")
        .values(raw_payload=null())
        .execution_options(synchronize_session=False)
    )
    descriptions = update(observation).where(observation.description_snapshot.is_not(None))
    if not compact:
        descriptions = descriptions.where(observation.observed_at < now - timedelta(days=7))
    await session.execute(
        descriptions.values(description_snapshot=None).execution_options(synchronize_session=False)
    )
    await session.execute(
        delete(CollectorRun)
        .where(
            CollectorRun.status != "started",
            or_(
                CollectorRun.finished_at < now - timedelta(days=30),
                (CollectorRun.finished_at.is_(None))
                & (CollectorRun.started_at < now - timedelta(days=30)),
            ),
        )
        .execution_options(synchronize_session=False)
    )
