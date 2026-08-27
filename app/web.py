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


@dataclass
class ListingStats:
    observations_count: int = 0
    price_changes_count: int = 0
    first_price_rub: int | None = None
    min_price_rub: int | None = None
    max_price_rub: int | None = None
    current_price_rub: int | None = None
    total_change_rub: int | None = None
    total_change_percent: float | None = None
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None


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
    price_min: int | None = Query(default=None, ge=0),
    price_max: int | None = Query(default=None, ge=0),
    price_m2_max: int | None = Query(default=None, ge=0),
    area_min: float | None = Query(default=None, ge=0),
    area_max: float | None = Query(default=None, ge=0),
    floor_min: int | None = Query(default=None, ge=0),
    floor_max: int | None = Query(default=None, ge=0),
    floors_total_max: int | None = Query(default=None, ge=0),
    district: str | None = Query(default=None),
    source: str | None = Query(default=None),
    changed_days: int | None = Query(default=None, ge=1, le=365),
    seen_days: int = Query(default=7, ge=1, le=365),
    sort: str = Query(default="price", pattern="^(price|price_m2|area|last_seen|newest)$"),
) -> ListingFilters:
    return ListingFilters(
        price_min=price_min,
        price_max=price_max,
        price_m2_max=price_m2_max,
        area_min=area_min,
        area_max=area_max,
        floor_min=floor_min,
        floor_max=floor_max,
        floors_total_max=floors_total_max,
        district=district.strip() if district else None,
        source=source.strip() if source else None,
        changed_days=changed_days,
        seen_days=seen_days,
        sort=sort,
    )


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    state: WebState = request.app.state.web
    async with state.session_factory() as session:
        yield session


FILTERS_DEP = Depends(parse_filters)
SESSION_DEP = Depends(db_session)


@app.get("/", response_class=HTMLResponse)
async def listings_page(
    request: Request,
    filters: ListingFilters = FILTERS_DEP,
    session: AsyncSession = SESSION_DEP,
) -> HTMLResponse:
    stmt = _filtered_listings_query(filters)
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = await session.scalar(count_stmt)
    listings = (await session.execute(stmt.limit(PAGE_SIZE))).scalars().all()
    stats = await _listing_stats(session, [item.id for item in listings])
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
    stats = await _listing_stats(session, [listing.id])
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
            "listing_stats": stats.get(listing.id, ListingStats()),
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
        case _:
            return stmt.order_by(Listing.price_rub.asc().nulls_last())


def _distinct_values(column) -> Select[tuple[str]]:
    return select(column).where(column.is_not(None)).distinct().order_by(column)


async def _listing_stats(
    session: AsyncSession,
    listing_ids: list[UUID],
) -> dict[UUID, ListingStats]:
    if not listing_ids:
        return {}

    stats = {listing_id: ListingStats() for listing_id in listing_ids}
    observations = (
        await session.execute(
            select(ListingObservation)
            .where(ListingObservation.listing_id.in_(listing_ids))
            .order_by(ListingObservation.listing_id, ListingObservation.observed_at)
        )
    ).scalars().all()
    for observation in observations:
        item = stats[observation.listing_id]
        item.observations_count += 1
        if item.first_observed_at is None:
            item.first_observed_at = observation.observed_at
            item.first_price_rub = observation.price_rub
        item.last_observed_at = observation.observed_at
        item.current_price_rub = observation.price_rub
        if observation.price_rub is None:
            continue
        if item.min_price_rub is None or observation.price_rub < item.min_price_rub:
            item.min_price_rub = observation.price_rub
        if item.max_price_rub is None or observation.price_rub > item.max_price_rub:
            item.max_price_rub = observation.price_rub

    change_counts = await session.execute(
        select(PriceHistory.listing_id, func.count())
        .where(PriceHistory.listing_id.in_(listing_ids))
        .group_by(PriceHistory.listing_id)
    )
    for listing_id, count in change_counts.all():
        stats[listing_id].price_changes_count = count

    for item in stats.values():
        if item.first_price_rub is None or item.current_price_rub is None:
            continue
        item.total_change_rub = item.current_price_rub - item.first_price_rub
        if item.first_price_rub:
            item.total_change_percent = item.total_change_rub / item.first_price_rub * 100

    return stats
