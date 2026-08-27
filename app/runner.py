from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser import persistent_context
from app.config import SearchConfig, Settings, load_searches, load_yaml
from app.db import create_engine, create_session_factory
from app.models import Base, CollectorRun, Search
from collectors import COLLECTORS
from collectors.debug import setup_debug
from services.ingestion import upsert_listing
from services.scoring import MarketStats, score_listing
from services.telegram import format_error_message, send_telegram

log = structlog.get_logger()


async def init_db(settings: Settings) -> None:
    engine = create_engine(settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def sync_search(session: AsyncSession, item: SearchConfig) -> Search:
    existing = (
        await session.execute(select(Search).where(Search.name == item.name))
    ).scalar_one_or_none()
    if existing is None:
        existing = Search(
            name=item.name, source=item.source, url=item.url, city=item.city, rooms=item.rooms
        )
        session.add(existing)
    existing.source = item.source
    existing.url = item.url
    existing.city = item.city
    existing.rooms = item.rooms
    existing.enabled = item.enabled
    existing.interval_hours = item.interval_hours
    existing.max_pages = item.max_pages
    await session.flush()
    return existing


async def collect_once(
    settings: Settings,
    *,
    only_source: str | None = None,
    only_search: str | None = None,
) -> None:
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    scoring_config = load_yaml(settings.scoring_config_path)
    searches_config = load_searches(settings.searches_config_path)
    async with session_factory() as session:
        async with session.begin():
            searches = [await sync_search(session, item) for item in searches_config]

    @asynccontextmanager
    async def browser():
        async for context in persistent_context(settings):
            yield context

    async with browser() as context:
        for search in searches:
            if not search.enabled:
                continue
            if only_source and search.source != only_source:
                continue
            if only_search and search.name != only_search:
                continue
            collector = COLLECTORS.get(search.source)
            if collector is None:
                continue
            async with session_factory() as session:
                async with session.begin():
                    db_search = await session.get(Search, search.id)
                    assert db_search is not None
                    run = CollectorRun(source=search.source, search_id=search.id, status="started")
                    db_search.last_started_at = datetime.now(UTC)
                    session.add(run)
                    await session.flush()
                    run_id = run.id
                try:
                    setup_debug(
                        collector,
                        run_id=run_id,
                        screenshots_dir=settings.screenshots_dir,
                        html_dir=settings.html_dumps_dir,
                    )
                    parsed = await collector.collect_search(search, context)
                    created = updated = price_changes = 0
                    async with session.begin():
                        db_search = await session.get(Search, search.id)
                        run = await session.get(CollectorRun, run_id)
                        assert db_search is not None and run is not None
                        for listing in parsed:
                            result = await upsert_listing(session, db_search, listing)
                            created += int(result.created)
                            updated += int(result.updated)
                            price_changes += int(result.price_changed)
                            score = score_listing(
                                price_per_m2=result.listing.price_per_m2,
                                area_total_m2=float(result.listing.area_total_m2)
                                if result.listing.area_total_m2
                                else None,
                                first_seen_at=result.listing.first_seen_at,
                                features=result.listing.features or {},
                                market=MarketStats(None, 0),
                                config=scoring_config,
                            )
                            result.listing.score = score.score
                            result.listing.score_details = score.score_details
                            result.listing.score_reasons = score.reasons
                        run.status = "completed"
                        run.finished_at = datetime.now(UTC)
                        run.listings_found = len(parsed)
                        run.listings_created = created
                        run.listings_updated = updated
                        run.price_changes_found = price_changes
                        db_search.last_status = "completed"
                        db_search.last_completed_at = datetime.now(UTC)
                    log.info(
                        "collect_completed",
                        source=search.source,
                        search_id=str(search.id),
                        found=len(parsed),
                    )
                except Exception as exc:
                    async with session.begin():
                        db_search = await session.get(Search, search.id)
                        run = await session.get(CollectorRun, run_id)
                        assert db_search is not None and run is not None
                        run.status = "failed"
                        run.finished_at = datetime.now(UTC)
                        run.error_message = str(exc)
                        run.debug_screenshot_path = getattr(
                            collector, "last_debug_screenshot_path", None
                        )
                        run.debug_html_path = getattr(collector, "last_debug_html_path", None)
                        db_search.last_status = "failed"
                        db_search.last_error = str(exc)
                        await send_telegram(settings, format_error_message(search.source, str(exc)))
                    log.exception("collect_failed", source=search.source, search_id=str(search.id))
    await engine.dispose()
