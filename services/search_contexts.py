from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DEFAULT_CONTEXT_SLUG, SearchContextConfig, load_search_config
from app.models import SearchContext


async def sync_contexts_from_config(
    session: AsyncSession,
    config_path: Path,
) -> dict[str, SearchContext]:
    config = load_search_config(config_path)
    contexts: dict[str, SearchContext] = {}
    for item in config.contexts:
        contexts[item.slug] = await upsert_context(session, item)
    return contexts


async def upsert_context(session: AsyncSession, item: SearchContextConfig) -> SearchContext:
    context = (
        await session.execute(select(SearchContext).where(SearchContext.slug == item.slug))
    ).scalar_one_or_none()
    if context is None:
        context = SearchContext(slug=item.slug, name=item.name)
        session.add(context)
    context.name = item.name
    context.object_type = item.object_type
    context.city = item.city
    context.expected_rooms = item.expected_rooms
    context.center_latitude = item.center_latitude
    context.center_longitude = item.center_longitude
    context.radius_km = item.radius_km
    context.enabled = item.enabled
    context.rules = item.rules
    await session.flush()
    return context


async def default_context(session: AsyncSession) -> SearchContext | None:
    return (
        await session.execute(
            select(SearchContext).where(SearchContext.slug == DEFAULT_CONTEXT_SLUG)
        )
    ).scalar_one_or_none()
