from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AIReviewJob,
    ApartmentGroup,
    ApartmentUserState,
    CollectorRun,
    DeletedContextSlug,
    Listing,
    ListingAIReview,
    ListingLink,
    ListingObservation,
    ListingUserState,
    Notification,
    PriceHistory,
    Search,
    SearchContext,
)
from scripts.compact_database import backup_database


async def context_deletion_plan(session: AsyncSession, context: SearchContext) -> dict:
    searches = list(await session.scalars(select(Search.id).where(Search.context_id == context.id)))
    candidates = set(
        await session.scalars(
            select(ListingObservation.listing_id).where(ListingObservation.search_id.in_(searches))
        )
    )
    shared = set(
        await session.scalars(
            select(ListingObservation.listing_id).where(
                ListingObservation.listing_id.in_(candidates),
                ListingObservation.search_id.not_in(searches),
            )
        )
    )
    # Preserve a whole apartment when another context still owns any source listing.
    groups = set(
        await session.scalars(
            select(Listing.group_id).where(
                Listing.id.in_(candidates), Listing.group_id.is_not(None)
            )
        )
    )
    shared_groups = set(
        await session.scalars(
            select(Listing.group_id).where(
                Listing.group_id.in_(groups), Listing.id.not_in(candidates - shared)
            )
        )
    )
    shared.update(
        await session.scalars(
            select(Listing.id).where(
                Listing.id.in_(candidates), Listing.group_id.in_(shared_groups)
            )
        )
    )
    reviews = list(
        await session.scalars(
            select(ListingAIReview.id).where(ListingAIReview.context_id == context.id)
        )
    )
    return {
        "searches": searches,
        "listings": sorted(candidates - shared, key=str),
        "shared": len(shared),
        "reviews": len(reviews),
    }


async def delete_context_data(
    session: AsyncSession, context: SearchContext, *, collector_locked: bool = False
) -> None:
    # Serialize with collector start and AI saves, including when SQLite FKs are disabled.
    await session.execute(
        update(SearchContext)
        .where(SearchContext.id == context.id)
        .values(enabled=SearchContext.enabled)
    )
    current = await session.scalar(
        select(SearchContext)
        .where(SearchContext.id == context.id)
        .execution_options(populate_existing=True)
    )
    if current is None:
        raise ValueError("Контекст уже удалён")
    plan = await context_deletion_plan(session, context)
    running = await session.scalar(
        select(CollectorRun.id)
        .where(CollectorRun.search_id.in_(plan["searches"]), CollectorRun.status == "started")
        .limit(1)
    )
    if running and not collector_locked:
        raise ValueError("Сейчас идёт сбор по контексту. Дождитесь его завершения.")
    ids = plan["listings"]
    groups = list(
        await session.scalars(
            select(Listing.group_id).where(Listing.id.in_(ids), Listing.group_id.is_not(None))
        )
    )
    await session.execute(
        delete(AIReviewJob).where(
            or_(AIReviewJob.context_id == context.id, AIReviewJob.listing_id.in_(ids))
        )
    )
    await session.execute(
        delete(ListingAIReview).where(
            or_(ListingAIReview.context_id == context.id, ListingAIReview.listing_id.in_(ids))
        )
    )
    await session.execute(
        delete(ListingObservation).where(
            or_(
                ListingObservation.search_id.in_(plan["searches"]),
                ListingObservation.listing_id.in_(ids),
            )
        )
    )
    for model in (PriceHistory, ListingUserState, Notification):
        await session.execute(delete(model).where(model.listing_id.in_(ids)))
    await session.execute(
        delete(ListingLink).where(
            or_(ListingLink.listing_id_a.in_(ids), ListingLink.listing_id_b.in_(ids))
        )
    )
    await session.execute(delete(Listing).where(Listing.id.in_(ids)))
    empty_groups = select(ApartmentGroup.id).where(
        ApartmentGroup.id.in_(groups),
        ~ApartmentGroup.id.in_(select(Listing.group_id).where(Listing.group_id.is_not(None))),
    )
    empty_ids = list(await session.scalars(empty_groups))
    await session.execute(
        delete(ApartmentUserState).where(ApartmentUserState.group_id.in_(empty_ids))
    )
    await session.execute(
        update(ListingAIReview).where(ListingAIReview.group_id.in_(empty_ids)).values(group_id=None)
    )
    await session.execute(delete(ApartmentGroup).where(ApartmentGroup.id.in_(empty_ids)))
    await session.execute(delete(CollectorRun).where(CollectorRun.search_id.in_(plan["searches"])))
    await session.execute(delete(Search).where(Search.id.in_(plan["searches"])))
    session.add(DeletedContextSlug(slug=context.slug))
    await session.execute(delete(SearchContext).where(SearchContext.id == context.id))


async def backup_before_context_delete(session: AsyncSession) -> Path:
    bind = session.get_bind()
    database = bind.engine.url.database
    if bind.dialect.name != "sqlite" or not database or database == ":memory:":
        raise ValueError("Очистка через интерфейс поддерживается только для файловой SQLite")
    return await asyncio.to_thread(_backup_file, database)


def _backup_file(database: str) -> Path:
    path = Path(database).resolve(strict=True)
    directory = path.parent / "backups"
    directory.mkdir(exist_ok=True)
    backup = directory / f"before-context-delete-{uuid4().hex}.sqlite3.gz"
    backup_database(path, backup)
    return backup


@contextmanager
def collector_guard(session: AsyncSession) -> Iterator[bool]:
    """Use the same LAN flock as scheduler; stale run rows do not imply a live process."""
    database = session.get_bind().engine.url.database
    if sys.platform == "win32" or not database or database == ":memory:":
        yield False
        return
    import fcntl

    lock_path = Path(database).resolve().parent / "collector.lock"
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Сейчас идёт сбор. Повторите удаление после его завершения.") from exc
        try:
            yield True
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
