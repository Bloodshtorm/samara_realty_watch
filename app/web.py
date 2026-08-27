from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Select, and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import create_engine, create_session_factory
from app.models import CollectorRun, Listing, ListingObservation, PriceHistory
from app.reporting_format import format_dt, format_m2, format_percent, format_rub
from services.analytics import (
    ListingHistoryStats,
    build_market_segments,
    listing_history_stats,
    recommend_listing,
    segment_key,
)

PAGE_SIZE = 100
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
    changed_days: int | None = None
    seen_days: int = 7
    sort: str = "price"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    engine = create_engine(settings)
    app.state.web = WebState(engine=engine, session_factory=create_session_factory(engine))
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="Samara Realty Watch", lifespan=lifespan)


def parse_filters(
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
    changed_days: str | None = Query(default=None),
    seen_days: str = Query(default="7"),
    sort: str = Query(default="price", pattern="^(price|price_m2|area|last_seen|newest|best)$"),
) -> ListingFilters:
    return ListingFilters(
        price_min=_optional_int("price_min", price_min, minimum=0),
        price_max=_optional_int("price_max", price_max, minimum=0),
        price_m2_max=_optional_int("price_m2_max", price_m2_max, minimum=0),
        area_min=_optional_float("area_min", area_min, minimum=0),
        area_max=_optional_float("area_max", area_max, minimum=0),
        floor_min=_optional_int("floor_min", floor_min, minimum=0),
        floor_max=_optional_int("floor_max", floor_max, minimum=0),
        floors_total_max=_optional_int("floors_total_max", floors_total_max, minimum=0),
        district=district.strip() if district else None,
        source=source.strip() if source else None,
        changed_days=_optional_int("changed_days", changed_days, minimum=1, maximum=365),
        seen_days=_optional_int("seen_days", seen_days, minimum=1, maximum=365) or 7,
        sort=sort,
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
    stmt = _filtered_listings_query(filters)
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = await session.scalar(count_stmt)
    candidate_limit = 500 if filters.sort == "best" else PAGE_SIZE
    listings = list((await session.execute(stmt.limit(candidate_limit))).scalars().all())
    stats = await listing_history_stats(session, [item.id for item in listings])
    segments = build_market_segments(await _recent_market_listings(session, filters.seen_days))
    recommendations = {
        item.id: recommend_listing(item, stats[item.id], segments.get(segment_key(item)))
        for item in listings
    }
    if filters.sort == "best":
        listings.sort(key=lambda item: recommendations[item.id].score, reverse=True)
    listings = listings[:PAGE_SIZE]
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
            "total": total or 0,
            "limit": PAGE_SIZE,
            "districts": districts,
            "sources": sources,
            "last_run": last_run,
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
        },
    )


def _filtered_listings_query(filters: ListingFilters) -> Select[tuple[Listing]]:
    conditions = [Listing.last_seen_at >= datetime.now(UTC) - timedelta(days=filters.seen_days)]
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
    if filters.changed_days is not None:
        changed_after = datetime.now(UTC) - timedelta(days=filters.changed_days)
        conditions.append(
            exists()
            .where(PriceHistory.listing_id == Listing.id)
            .where(PriceHistory.observed_at >= changed_after)
        )

    stmt = select(Listing).where(and_(*conditions))
    match filters.sort:
        case "price_m2":
            return stmt.order_by(Listing.price_per_m2.asc().nulls_last())
        case "area":
            return stmt.order_by(Listing.area_total_m2.desc().nulls_last())
        case "last_seen":
            return stmt.order_by(Listing.last_seen_at.desc())
        case "newest":
            return stmt.order_by(Listing.first_seen_at.desc())
        case "best":
            return stmt.order_by(Listing.last_seen_at.desc())
        case _:
            return stmt.order_by(Listing.price_rub.asc().nulls_last())


def _distinct_values(column) -> Select[tuple[str]]:
    return select(column).where(column.is_not(None)).distinct().order_by(column)


async def _recent_market_listings(session: AsyncSession, seen_days: int) -> list[Listing]:
    cutoff = datetime.now(UTC) - timedelta(days=seen_days)
    return list(
        (
            await session.execute(
                select(Listing).where(
                    Listing.last_seen_at >= cutoff,
                    Listing.price_rub.is_not(None),
                    Listing.price_per_m2.is_not(None),
                    Listing.area_total_m2.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
