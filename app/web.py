from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import create_engine, create_session_factory
from app.models import (
    Base,
    CollectorRun,
    Listing,
    ListingObservation,
    ListingUserState,
    PriceHistory,
    Search,
    SearchContext,
)
from app.reporting_format import format_dt, format_m2, format_percent, format_rub
from services.analytics import (
    ListingHistoryStats,
    build_market_segments,
    listing_history_stats,
    recommend_listing,
    segment_key,
)
from services.search_contexts import sync_contexts_from_config

PAGE_SIZE = 100
SORT_VALUES = (
    "price",
    "price_desc",
    "price_m2",
    "price_m2_desc",
    "area",
    "area_asc",
    "floor",
    "floor_desc",
    "last_seen",
    "newest",
    "best",
    "score_asc",
)
VIEW_VALUES = ("active", "favorites", "hidden")
MORTGAGE_VALUES = ("", "available")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["rub"] = format_rub
templates.env.filters["m2"] = format_m2
templates.env.filters["dt"] = format_dt
templates.env.filters["percent"] = format_percent


@dataclass
class WebState:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]


@dataclass
class ListingFilters:
    context: str = "3rooms_samara"
    price_min: int | None = None
    price_max: int | None = None
    price_m2_max: int | None = None
    area_min: float | None = None
    area_max: float | None = None
    floor_min: int | None = None
    floor_max: int | None = None
    floors_total_max: int | None = None
    district: str | None = None
    source: str | None = None
    mortgage: str = ""
    changed_days: int | None = None
    seen_days: int = 7
    sort: str = "price"
    view: str = "active"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    engine = create_engine(settings)
    app.state.web = WebState(engine=engine, session_factory=create_session_factory(engine))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        async with session.begin():
            await sync_contexts_from_config(session, settings.searches_config_path)
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="Samara Realty Watch", lifespan=lifespan)


def parse_filters(
    context: str = Query(default="3rooms_samara"),
    price_min: str | None = Query(default=None),
    price_max: str | None = Query(default=None),
    price_m2_max: str | None = Query(default=None),
    area_min: str | None = Query(default=None),
    area_max: str | None = Query(default=None),
    floor_min: str | None = Query(default=None),
    floor_max: str | None = Query(default=None),
    floors_total_max: str | None = Query(default=None),
    district: str | None = Query(default=None),
    source: str | None = Query(default=None),
    mortgage: str = Query(default="", pattern="^(|available)$"),
    changed_days: str | None = Query(default=None),
    seen_days: str = Query(default="7"),
    sort: str = Query(
        default="price",
        pattern=(
            "^(price|price_desc|price_m2|price_m2_desc|area|area_asc|floor|floor_desc|"
            "last_seen|newest|best|score_asc)$"
        ),
    ),
    view: str = Query(default="active", pattern="^(active|favorites|hidden)$"),
) -> ListingFilters:
    context_value = _optional_text(context) or "3rooms_samara"
    district_value = _optional_text(district)
    source_value = _optional_text(source)
    mortgage_value = _optional_text(mortgage) or ""
    return ListingFilters(
        context=context_value,
        price_min=_optional_int("price_min", price_min, minimum=0),
        price_max=_optional_int("price_max", price_max, minimum=0),
        price_m2_max=_optional_int("price_m2_max", price_m2_max, minimum=0),
        area_min=_optional_float("area_min", area_min, minimum=0),
        area_max=_optional_float("area_max", area_max, minimum=0),
        floor_min=_optional_int("floor_min", floor_min, minimum=0),
        floor_max=_optional_int("floor_max", floor_max, minimum=0),
        floors_total_max=_optional_int("floors_total_max", floors_total_max, minimum=0),
        district=district_value,
        source=source_value,
        mortgage=mortgage_value if mortgage_value in MORTGAGE_VALUES else "",
        changed_days=_optional_int("changed_days", changed_days, minimum=1, maximum=365),
        seen_days=_optional_int("seen_days", seen_days, minimum=1, maximum=365) or 7,
        sort=sort if sort in SORT_VALUES else "price",
        view=view if view in VIEW_VALUES else "active",
    )


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    state: WebState = request.app.state.web
    async with state.session_factory() as session:
        yield session


FILTERS_DEP = Depends(parse_filters)
SESSION_DEP = Depends(db_session)


def _optional_int(
    name: str,
    value: str | None,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    cleaned = value.strip() if value is not None else ""
    if not cleaned:
        return None
    try:
        parsed = int(cleaned)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{name} must be an integer") from exc
    if minimum is not None and parsed < minimum:
        raise HTTPException(status_code=422, detail=f"{name} must be >= {minimum}")
    if maximum is not None and parsed > maximum:
        raise HTTPException(status_code=422, detail=f"{name} must be <= {maximum}")
    return parsed


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _optional_float(
    name: str,
    value: str | None,
    *,
    minimum: float | None = None,
) -> float | None:
    cleaned = value.strip() if value is not None else ""
    if not cleaned:
        return None
    try:
        parsed = float(cleaned.replace(",", "."))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{name} must be a number") from exc
    if minimum is not None and parsed < minimum:
        raise HTTPException(status_code=422, detail=f"{name} must be >= {minimum}")
    return parsed


@app.get("/", response_class=HTMLResponse)
async def listings_page(
    request: Request,
    filters: ListingFilters = FILTERS_DEP,
    session: AsyncSession = SESSION_DEP,
) -> HTMLResponse:
    contexts = await _contexts(session)
    selected_context = _selected_context(contexts, filters.context)
    filters.context = selected_context.slug
    stmt = _filtered_listings_query(filters, selected_context)
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = await session.scalar(count_stmt)
    candidate_limit = 500 if filters.sort in {"best", "score_asc"} else PAGE_SIZE
    listings = list((await session.execute(stmt.limit(candidate_limit))).scalars().all())
    stats = await listing_history_stats(session, [item.id for item in listings])
    segments = build_market_segments(
        await _recent_market_listings(session, filters.seen_days, selected_context)
    )
    recommendations = {
        item.id: recommend_listing(item, stats[item.id], segments.get(segment_key(item)))
        for item in listings
    }
    if filters.sort in {"best", "score_asc"}:
        listings.sort(
            key=lambda item: recommendations[item.id].score,
            reverse=filters.sort == "best",
        )
    listings = listings[:PAGE_SIZE]
    user_states = await _user_states(session, [item.id for item in listings])
    districts = (await session.execute(_distinct_values(Listing.district))).scalars().all()
    sources = (await session.execute(_distinct_values(Listing.source))).scalars().all()
    last_run = (
        await session.execute(
            select(CollectorRun).order_by(CollectorRun.started_at.desc()).limit(1)
        )
    ).scalar_one_or_none()

    return templates.TemplateResponse(
        request,
        "listings.html",
        {
            "filters": filters,
            "listings": listings,
            "stats": stats,
            "recommendations": recommendations,
            "user_states": user_states,
            "total": total or 0,
            "limit": PAGE_SIZE,
            "districts": districts,
            "sources": sources,
            "last_run": last_run,
            "contexts": contexts,
            "selected_context": selected_context,
            "map_points": Markup(
                json.dumps(_map_points(listings, user_states), ensure_ascii=False)
            ),
            "sort_url": _sort_url,
            "view_url": _view_url,
            "context_url": _context_url,
        },
    )


@app.get("/listings/{listing_id}", response_class=HTMLResponse)
async def listing_detail(
    request: Request,
    listing_id: UUID,
    session: AsyncSession = SESSION_DEP,
) -> HTMLResponse:
    listing = await session.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    observations = (
        await session.execute(
            select(ListingObservation)
            .where(ListingObservation.listing_id == listing.id)
            .order_by(ListingObservation.observed_at.desc())
            .limit(200)
        )
    ).scalars().all()
    stats = await listing_history_stats(session, [listing.id])
    segments = build_market_segments(await _recent_market_listings(session, 180))
    listing_stats = stats.get(listing.id, ListingHistoryStats())
    recommendation = recommend_listing(
        listing,
        listing_stats,
        segments.get(segment_key(listing)),
    )
    price_changes = (
        await session.execute(
            select(PriceHistory)
            .where(PriceHistory.listing_id == listing.id)
            .order_by(PriceHistory.observed_at.desc())
        )
    ).scalars().all()

    return templates.TemplateResponse(
        request,
        "listing_detail.html",
        {
            "listing": listing,
            "listing_stats": listing_stats,
            "recommendation": recommendation,
            "observations": observations,
            "price_changes": price_changes,
            "price_timeline": _price_timeline(list(reversed(observations))),
            "user_state": await _user_state(session, listing.id),
        },
    )


@app.get("/contexts/new", response_class=HTMLResponse)
async def new_context_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "context_form.html",
        {
            "sources": ("avito", "cian", "domclick", "yandex_realty"),
            "defaults": {
                "object_type": "flat",
                "city": "Самара",
                "radius_km": "50",
            },
        },
    )


@app.post("/contexts")
async def create_context(
    request: Request,
    session: AsyncSession = SESSION_DEP,
) -> RedirectResponse:
    body = (await request.body()).decode()
    form = parse_qs(body, keep_blank_values=True)
    name = _form_value(form, "name")
    object_type = _form_value(form, "object_type") or "flat"
    city = _form_value(form, "city") or "Самара"
    rooms_raw = _form_value(form, "expected_rooms")
    radius_raw = _form_value(form, "radius_km")
    sources = form.get("sources", [])
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    if object_type not in {"flat", "land"}:
        raise HTTPException(status_code=422, detail="object_type must be flat or land")
    expected_rooms = int(rooms_raw) if rooms_raw else None
    radius_km = float(radius_raw.replace(",", ".")) if radius_raw else None
    slug = _slugify(name)
    context = (
        await session.execute(select(SearchContext).where(SearchContext.slug == slug))
    ).scalar_one_or_none()
    if context is not None:
        raise HTTPException(status_code=409, detail="context already exists")
    context = SearchContext(
        slug=slug,
        name=name,
        object_type=object_type,
        city=city,
        expected_rooms=expected_rooms,
        center_latitude=53.195873,
        center_longitude=50.100193,
        radius_km=radius_km,
        enabled=True,
        rules={"created_from_ui": True},
    )
    session.add(context)
    await session.flush()
    for source in sources:
        url = _generated_search_url(source, object_type, expected_rooms)
        if url is None:
            continue
        session.add(
            Search(
                context_id=context.id,
                name=f"{slug}_{source}",
                source=source,
                url=url,
                city=city,
                rooms=expected_rooms if expected_rooms is not None else 0,
                enabled=False,
                interval_hours=12,
                max_pages=20,
            )
        )
    await session.commit()
    return RedirectResponse(f"/?context={context.slug}", status_code=303)


@app.post("/listings/{listing_id}/state")
async def update_listing_state(
    request: Request,
    listing_id: UUID,
    action: str = Query(pattern="^(favorite|unfavorite|hide|unhide)$"),
    session: AsyncSession = SESSION_DEP,
) -> RedirectResponse:
    listing = await session.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")

    state = await _user_state(session, listing_id)
    if state is None:
        state = ListingUserState(listing_id=listing_id)
        session.add(state)

    match action:
        case "favorite":
            state.is_favorite = True
        case "unfavorite":
            state.is_favorite = False
        case "hide":
            state.is_hidden = True
        case "unhide":
            state.is_hidden = False

    await session.commit()
    return RedirectResponse(request.headers.get("referer") or "/", status_code=303)


def _filtered_listings_query(
    filters: ListingFilters,
    context: SearchContext,
) -> Select[tuple[Listing]]:
    conditions = [
        Listing.is_active.is_(True),
        Listing.last_seen_at >= datetime.now(UTC) - timedelta(days=filters.seen_days),
        Search.context_id == context.id,
    ]
    if context.expected_rooms is not None:
        conditions.append(Listing.rooms == context.expected_rooms)
    if filters.price_min is not None:
        conditions.append(Listing.price_rub >= filters.price_min)
    if filters.price_max is not None:
        conditions.append(Listing.price_rub <= filters.price_max)
    if filters.price_m2_max is not None:
        conditions.append(Listing.price_per_m2 <= filters.price_m2_max)
    if filters.area_min is not None:
        conditions.append(Listing.area_total_m2 >= filters.area_min)
    if filters.area_max is not None:
        conditions.append(Listing.area_total_m2 <= filters.area_max)
    if filters.floor_min is not None:
        conditions.append(Listing.floor >= filters.floor_min)
    if filters.floor_max is not None:
        conditions.append(Listing.floor <= filters.floor_max)
    if filters.floors_total_max is not None:
        conditions.append(Listing.floors_total <= filters.floors_total_max)
    if filters.district:
        conditions.append(Listing.district == filters.district)
    if filters.source:
        conditions.append(Listing.source == filters.source)
    if filters.mortgage == "available":
        conditions.append(
            or_(
                Listing.features["mortgage_available"].as_boolean().is_(True),
                Listing.features["family_mortgage"].as_boolean().is_(True),
                Listing.features["it_mortgage"].as_boolean().is_(True),
                Listing.features["subsidized_mortgage"].as_boolean().is_(True),
            )
        )
    if filters.changed_days is not None:
        changed_after = datetime.now(UTC) - timedelta(days=filters.changed_days)
        conditions.append(
            exists()
            .where(PriceHistory.listing_id == Listing.id)
            .where(PriceHistory.observed_at >= changed_after)
        )

    match filters.view:
        case "favorites":
            conditions.append(ListingUserState.is_favorite.is_(True))
            conditions.append(
                or_(ListingUserState.is_hidden.is_(False), ListingUserState.is_hidden.is_(None))
            )
        case "hidden":
            conditions.append(ListingUserState.is_hidden.is_(True))
        case _:
            conditions.append(
                or_(ListingUserState.is_hidden.is_(False), ListingUserState.is_hidden.is_(None))
            )

    stmt = (
        select(Listing)
        .join(ListingObservation, ListingObservation.listing_id == Listing.id)
        .join(Search, ListingObservation.search_id == Search.id)
        .outerjoin(ListingUserState, ListingUserState.listing_id == Listing.id)
        .where(and_(*conditions))
        .distinct()
    )
    match filters.sort:
        case "price_desc":
            return stmt.order_by(Listing.price_rub.desc().nulls_last())
        case "price_m2":
            return stmt.order_by(Listing.price_per_m2.asc().nulls_last())
        case "price_m2_desc":
            return stmt.order_by(Listing.price_per_m2.desc().nulls_last())
        case "area":
            return stmt.order_by(Listing.area_total_m2.desc().nulls_last())
        case "area_asc":
            return stmt.order_by(Listing.area_total_m2.asc().nulls_last())
        case "floor":
            return stmt.order_by(Listing.floor.asc().nulls_last())
        case "floor_desc":
            return stmt.order_by(Listing.floor.desc().nulls_last())
        case "last_seen":
            return stmt.order_by(Listing.last_seen_at.desc())
        case "newest":
            return stmt.order_by(Listing.first_seen_at.desc())
        case "best":
            return stmt.order_by(Listing.last_seen_at.desc())
        case _:
            return stmt.order_by(Listing.price_rub.asc().nulls_last())


def _listing_in_context(context: SearchContext):
    return (
        exists()
        .where(ListingObservation.listing_id == Listing.id)
        .where(ListingObservation.search_id == Search.id)
        .where(Search.context_id == context.id)
    )


def _distinct_values(column) -> Select[tuple[str]]:
    return select(column).where(column.is_not(None)).distinct().order_by(column)


async def _contexts(session: AsyncSession) -> list[SearchContext]:
    return list(
        (
            await session.execute(
                select(SearchContext)
                .where(SearchContext.enabled.is_(True))
                .order_by(SearchContext.created_at, SearchContext.name)
            )
        )
        .scalars()
        .all()
    )


def _selected_context(contexts: list[SearchContext], slug: str) -> SearchContext:
    if not contexts:
        raise HTTPException(status_code=500, detail="No search contexts configured")
    for context in contexts:
        if context.slug == slug:
            return context
    return contexts[0]


def _sort_url(request: Request, sort: str) -> str:
    params = dict(request.query_params)
    params["sort"] = sort
    return f"?{urlencode(params)}"


def _view_url(request: Request, view: str) -> str:
    params = dict(request.query_params)
    params["view"] = view
    return f"?{urlencode(params)}"


def _context_url(request: Request, context_slug: str) -> str:
    params = dict(request.query_params)
    params["context"] = context_slug
    return f"?{urlencode(params)}"


def _map_points(
    listings: list[Listing],
    user_states: dict[UUID, ListingUserState],
) -> list[dict[str, object]]:
    points = []
    for item in listings:
        if item.latitude is None or item.longitude is None:
            continue
        points.append(
            {
                "id": str(item.id),
                "lat": item.latitude,
                "lng": item.longitude,
                "price": format_rub(item.price_rub),
                "title": item.address_normalized or item.address_raw or item.title or "-",
                "source": item.source,
                "score": item.score,
                "url": f"/listings/{item.id}",
                "favorite_action": "unfavorite"
                if user_states.get(item.id) and user_states[item.id].is_favorite
                else "favorite",
                "favorite_label": "Убрать из избранного"
                if user_states.get(item.id) and user_states[item.id].is_favorite
                else "В избранное",
            }
        )
    return points


def _price_timeline(observations: list[ListingObservation]) -> dict[str, object] | None:
    priced = [item for item in observations if item.price_rub is not None]
    if len(priced) < 2:
        return None
    width = 720
    height = 220
    pad_x = 46
    pad_y = 28
    prices = [item.price_rub for item in priced if item.price_rub is not None]
    min_price = min(prices)
    max_price = max(prices)
    price_span = max(max_price - min_price, 1)
    time_start = priced[0].observed_at.timestamp()
    time_end = priced[-1].observed_at.timestamp()
    time_span = max(time_end - time_start, 1)
    coords = []
    for item in priced:
        assert item.price_rub is not None
        x = pad_x + (item.observed_at.timestamp() - time_start) / time_span * (width - pad_x * 2)
        y = height - pad_y - (item.price_rub - min_price) / price_span * (height - pad_y * 2)
        coords.append((round(x, 1), round(y, 1), item))
    start_price = priced[0].price_rub
    end_price = priced[-1].price_rub
    assert start_price is not None and end_price is not None
    trend = end_price - start_price
    return {
        "width": width,
        "height": height,
        "points": " ".join(f"{x},{y}" for x, y, _ in coords),
        "circles": [
            {
                "x": x,
                "y": y,
                "label": f"{format_dt(item.observed_at)} · {format_rub(item.price_rub)}",
            }
            for x, y, item in coords
        ],
        "min_price": format_rub(min_price),
        "max_price": format_rub(max_price),
        "start_date": format_dt(priced[0].observed_at),
        "end_date": format_dt(priced[-1].observed_at),
        "trend_class": "down" if trend < 0 else "up",
        "trend_label": format_rub(trend),
    }


def _form_value(form: dict[str, list[str]], name: str) -> str:
    values = form.get(name, [""])
    return values[0].strip()


def _slugify(value: str) -> str:
    mapping = {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "c",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "ы": "y",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
    normalized = "".join(mapping.get(char, char) for char in value.lower())
    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return slug or "context"


def _generated_search_url(source: str, object_type: str, rooms: int | None) -> str | None:
    if object_type == "land":
        urls = {
            "avito": "https://www.avito.ru/samara/zemelnye_uchastki/prodam-ASgBAgICAUSWA9AQAUDmBxSM",
            "cian": "https://samara.cian.ru/kupit-zemelniy-uchastok/",
        }
        return urls.get(source)
    if rooms is None:
        return None
    if source == "cian":
        room_param = "room0=1" if rooms == 0 else f"room{rooms}=1"
        return (
            "https://samara.cian.ru/cat.php?deal_type=sale&engine_version=2"
            f"&offer_type=flat&region=4966&{room_param}"
        )
    if source == "yandex_realty" and rooms == 3:
        return "https://realty.yandex.ru/samara/kupit/kvartira/tryohkomnatnaya/"
    return None


async def _user_state(session: AsyncSession, listing_id: UUID) -> ListingUserState | None:
    return (
        await session.execute(
            select(ListingUserState).where(ListingUserState.listing_id == listing_id)
        )
    ).scalar_one_or_none()


async def _user_states(
    session: AsyncSession,
    listing_ids: list[UUID],
) -> dict[UUID, ListingUserState]:
    if not listing_ids:
        return {}
    rows = (
        await session.execute(
            select(ListingUserState).where(ListingUserState.listing_id.in_(listing_ids))
        )
    ).scalars()
    return {state.listing_id: state for state in rows}


async def _recent_market_listings(
    session: AsyncSession,
    seen_days: int,
    context: SearchContext | None = None,
) -> list[Listing]:
    cutoff = datetime.now(UTC) - timedelta(days=seen_days)
    conditions = [
        Listing.is_active.is_(True),
        Listing.last_seen_at >= cutoff,
        Listing.price_rub.is_not(None),
        Listing.price_per_m2.is_not(None),
        Listing.area_total_m2.is_not(None),
    ]
    if context is not None:
        conditions.append(Search.context_id == context.id)
    if context is not None and context.expected_rooms is not None:
        conditions.append(Listing.rooms == context.expected_rooms)
    return list(
        (
            await session.execute(
                select(Listing)
                .join(ListingObservation, ListingObservation.listing_id == Listing.id)
                .join(Search, ListingObservation.search_id == Search.id)
                .where(*conditions)
                .distinct()
            )
        )
        .scalars()
        .all()
    )
