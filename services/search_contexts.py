from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DEFAULT_CONTEXT_SLUG, SearchContextConfig, load_search_config
from app.models import DeletedContextSlug, SearchContext

CONTEXT_FIELDS = (
    "price_min",
    "price_max",
    "price_m2_max",
    "area_min",
    "area_max",
    "floor_min",
    "floor_max",
    "floors_total_max",
    "district",
)


def context_rules(context: SearchContext) -> dict:
    return {
        **(context.rules or {}),
        **{
            key: getattr(context, key)
            for key in CONTEXT_FIELDS
            if getattr(context, key) is not None
        },
    }


def set_context_fields(context: SearchContext, values: dict) -> None:
    for key in CONTEXT_FIELDS:
        value = values.get(key)
        if value not in (None, "") and key != "district":
            value = float(str(value).replace(",", "."))
            if not key.startswith("area"):
                value = int(value)
        setattr(context, key, value if value != "" else None)
    context.ai_preferences = values.get("ai_preferences") or None


async def sync_contexts_from_config(
    session: AsyncSession,
    config_path: Path,
) -> dict[str, SearchContext]:
    config = load_search_config(config_path)
    contexts: dict[str, SearchContext] = {}
    for item in config.contexts:
        context = await upsert_context(session, item)
        if context is not None:
            contexts[item.slug] = context
    return contexts


async def upsert_context(session: AsyncSession, item: SearchContextConfig) -> SearchContext | None:
    if await session.get(DeletedContextSlug, item.slug) is not None:
        return None
    context = (
        await session.execute(select(SearchContext).where(SearchContext.slug == item.slug))
    ).scalar_one_or_none()
    if context is None:
        context = SearchContext(slug=item.slug, name=item.name)
        session.add(context)
    else:
        return context
    context.name = item.name
    context.object_type = item.object_type
    context.city = item.city
    context.expected_rooms = item.expected_rooms
    context.center_latitude = item.center_latitude
    context.center_longitude = item.center_longitude
    context.radius_km = item.radius_km
    context.enabled = item.enabled
    context.rules = {
        key: value
        for key, value in item.rules.items()
        if key not in (*CONTEXT_FIELDS, "ai_preferences")
    }
    set_context_fields(context, item.rules)
    await session.flush()
    return context


async def default_context(session: AsyncSession) -> SearchContext | None:
    return (
        await session.execute(
            select(SearchContext).where(SearchContext.slug == DEFAULT_CONTEXT_SLUG)
        )
    ).scalar_one_or_none()
