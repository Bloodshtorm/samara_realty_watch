from __future__ import annotations

import hashlib
import itertools
import json
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, TypedDict
from urllib.parse import parse_qs, urlencode
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pydantic import BaseModel, Field
from sqlalchemy import Select, and_, exists, func, or_, select, true, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased, defer
from sqlalchemy.sql.elements import ColumnElement

from app.config import Settings
from app.db import create_engine, create_session_factory
from app.models import (
    ApartmentGroup,
    ApartmentUserState,
    Base,
    CollectorRun,
    Listing,
    ListingLink,
    ListingObservation,
    ListingUserState,
    PriceHistory,
    Search,
    SearchContext,
    User,
    UserSession,
)
from app.reporting_format import format_dt, format_m2, format_percent, format_rub
from services.analytics import (
    ListingHistoryStats,
    ListingRecommendation,
    build_market_segments,
    listing_history_stats,
    recommend_listing,
    segment_key,
)
from services.apartments import confirm_link, group_members, set_link, split_member
from services.auth import (
    SESSION_COOKIE_NAME,
    bootstrap_admin,
    create_user_session,
    hash_password,
    hash_session_token,
    verify_password,
)
from services.avito_policy import AvitoPolicy
from services.deduplication import building_key
from services.geography import in_context, usable_coordinates
from services.search_contexts import sync_contexts_from_config

PAGE_SIZE = 100
MAP_POINTS_LIMIT = 5_000
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
SOURCE_CHOICES = ("avito", "cian", "domclick", "etagi", "mirkvartir", "n1", "yandex_realty")
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
    q: str | None = None
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
    location: str = "within"


class SpatialFilterPayload(BaseModel):
    mode: Literal["bounds", "polygon"]
    north: float | None = None
    south: float | None = None
    east: float | None = None
    west: float | None = None
    polygon: list[tuple[float, float]] = Field(default_factory=list)


class TableRowsContext(TypedDict):
    listings: list[Listing]
    stats: dict[UUID, ListingHistoryStats]
    recommendations: dict[UUID, ListingRecommendation]
    user_states: dict[UUID, ListingUserState]
    apartments: dict[UUID, dict]


class CollectorRunView(TypedDict):
    run: CollectorRun
    search_name: str
    duration_label: str


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
            await bootstrap_admin(session, settings)
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="Samara Realty Watch", lifespan=lifespan)


def parse_filters(
    context: str = Query(default="3rooms_samara"),
    q: str | None = Query(default=None),
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
    location: str = Query(default="within", pattern="^(within|unknown)$"),
) -> ListingFilters:
    context_value = _optional_text(context) or "3rooms_samara"
    q_value = _optional_text(q)
    district_value = _optional_text(district)
    source_value = _optional_text(source)
    mortgage_value = _optional_text(mortgage) or ""
    return ListingFilters(
        context=context_value,
        q=q_value,
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
        location=location if location in ("within", "unknown") else "within",
    )


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    state: WebState = request.app.state.web
    async with state.session_factory() as session:
        yield session


FILTERS_DEP = Depends(parse_filters)
SESSION_DEP = Depends(db_session)


async def current_user(request: Request, session: AsyncSession = SESSION_DEP) -> User:
    user_count = await session.scalar(select(func.count()).select_from(User))
    if not user_count:
        raise HTTPException(
            status_code=503,
            detail="Set APP_ADMIN_USERNAME and APP_ADMIN_PASSWORD to bootstrap the first admin.",
        )
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=303, headers={"Location": _login_url(request)})
    now = datetime.now(UTC)
    row = (
        await session.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(UserSession.token_hash == hash_session_token(token))
            .where(UserSession.expires_at > now)
            .where(User.is_active.is_(True))
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=303, headers={"Location": _login_url(request)})
    user_session, user = row
    user_session.last_seen_at = now
    await session.commit()
    return user


USER_DEP = Depends(current_user)


async def current_admin(user: User = USER_DEP) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role is required")
    return user


ADMIN_DEP = Depends(current_admin)


@app.get("/healthz", include_in_schema=False)
async def healthz(session: AsyncSession = SESSION_DEP) -> JSONResponse:
    try:
        await session.execute(select(1))
    except SQLAlchemyError:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok"})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, session: AsyncSession = SESSION_DEP) -> HTMLResponse:
    has_users = bool(await session.scalar(select(func.count()).select_from(User)))
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "next": request.query_params.get("next", "/"),
            "error": None,
            "has_users": has_users,
        },
    )


@app.post("/login")
async def login_submit(request: Request, session: AsyncSession = SESSION_DEP) -> Response:
    body = (await request.body()).decode()
    form = parse_qs(body, keep_blank_values=True)
    username = _form_value(form, "username").lower()
    password = _form_value(form, "password")
    next_url = _safe_next(_form_value(form, "next") or "/")
    user = (
        await session.execute(select(User).where(func.lower(User.username) == username))
    ).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "next": next_url,
                "error": "Неверный логин или пароль.",
                "has_users": bool(await session.scalar(select(func.count()).select_from(User))),
            },
            status_code=401,
        )
    settings = Settings()
    _, token = await create_user_session(session, user, days=settings.app_session_days)
    await session.commit()
    response = RedirectResponse(next_url, status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=settings.app_session_days * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/logout")
@app.post("/logout")
async def logout(request: Request, session: AsyncSession = SESSION_DEP) -> RedirectResponse:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        user_session = (
            await session.execute(
                select(UserSession).where(UserSession.token_hash == hash_session_token(token))
            )
        ).scalar_one_or_none()
        if user_session is not None:
            await session.delete(user_session)
            await session.commit()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(
    request: Request,
    session: AsyncSession = SESSION_DEP,
    admin: User = ADMIN_DEP,
) -> HTMLResponse:
    users = (
        (await session.execute(select(User).order_by(User.created_at, User.username)))
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "admin_users.html",
        {"users": users, "current_user": admin, "error": None},
    )


@app.post("/admin/users")
async def admin_create_user(
    request: Request,
    session: AsyncSession = SESSION_DEP,
    _admin: User = ADMIN_DEP,
) -> Response:
    body = (await request.body()).decode()
    form = parse_qs(body, keep_blank_values=True)
    username = _form_value(form, "username").lower()
    display_name = _form_value(form, "display_name") or username
    password = _form_value(form, "password")
    role = _form_value(form, "role") or "user"
    if not username or not password:
        return await _admin_users_error(request, session, "Логин и пароль обязательны.")
    if role not in {"admin", "user"}:
        return await _admin_users_error(request, session, "Некорректная роль.")
    exists_user = (
        await session.execute(select(User).where(func.lower(User.username) == username))
    ).scalar_one_or_none()
    if exists_user is not None:
        return await _admin_users_error(request, session, "Пользователь уже существует.")
    session.add(
        User(
            username=username,
            display_name=display_name,
            role=role,
            password_hash=hash_password(password),
            is_active=True,
        )
    )
    await session.commit()
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/password")
async def admin_reset_password(
    user_id: UUID,
    request: Request,
    session: AsyncSession = SESSION_DEP,
    _admin: User = ADMIN_DEP,
) -> RedirectResponse:
    body = (await request.body()).decode()
    password = _form_value(parse_qs(body, keep_blank_values=True), "password")
    if not password:
        raise HTTPException(status_code=422, detail="password is required")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.password_hash = hash_password(password)
    sessions = (
        await session.scalars(select(UserSession).where(UserSession.user_id == user.id))
    ).all()
    for user_session in sessions:
        await session.delete(user_session)
    await session.commit()
    return RedirectResponse("/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/state")
async def admin_user_state(
    user_id: UUID,
    action: str = Query(pattern="^(enable|disable)$"),
    session: AsyncSession = SESSION_DEP,
    admin: User = ADMIN_DEP,
) -> RedirectResponse:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id and action == "disable":
        raise HTTPException(status_code=409, detail="Cannot disable current admin")
    user.is_active = action == "enable"
    if not user.is_active:
        sessions = (
            await session.scalars(select(UserSession).where(UserSession.user_id == user.id))
        ).all()
        for user_session in sessions:
            await session.delete(user_session)
    await session.commit()
    return RedirectResponse("/admin/users", status_code=303)


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


def _optional_rule_text(rules: dict, name: str) -> str | None:
    value = rules.get(name)
    if not isinstance(value, str):
        return None
    return _optional_text(value)


def _optional_rule_int(rules: dict, name: str) -> int | None:
    value = rules.get(name)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return _optional_int(name, value, minimum=0)
    return None


def _optional_rule_float(rules: dict, name: str) -> float | None:
    value = rules.get(name)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        return _optional_float(name, value, minimum=0)
    return None


def _safe_next(value: str) -> str:
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _login_url(request: Request) -> str:
    next_url = str(request.url.path)
    if request.url.query:
        next_url = f"{next_url}?{request.url.query}"
    return "/login?" + urlencode({"next": _safe_next(next_url)})


async def _admin_users_error(request: Request, session: AsyncSession, error: str) -> HTMLResponse:
    users = (
        (await session.execute(select(User).order_by(User.created_at, User.username)))
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "admin_users.html",
        {"users": users, "current_user": None, "error": error},
        status_code=422,
    )


@app.get("/", response_class=HTMLResponse)
async def listings_page(
    request: Request,
    filters: ListingFilters = FILTERS_DEP,
    session: AsyncSession = SESSION_DEP,
    user: User = USER_DEP,
) -> Response:
    contexts = await _contexts(session, user)
    if not contexts:
        return RedirectResponse("/contexts/new", status_code=303)
    selected_context = _selected_context(contexts, filters.context)
    filters.context = selected_context.slug
    stmt = _filtered_listings_query(filters, selected_context, user)
    row_candidates = unique_apartments(
        [
            item
            for item in (await session.scalars(stmt)).all()
            if in_context(item, selected_context, filters.location)
        ]
    )
    total = len(row_candidates)
    row_context = await _table_rows_context(
        session, row_candidates, filters, selected_context, user
    )
    map_listings = row_candidates[:MAP_POINTS_LIMIT]
    map_user_states = await _user_states(session, [item.id for item in map_listings], user)
    districts = (await session.execute(_distinct_values(Listing.district))).scalars().all()
    sources = (await session.execute(_distinct_values(Listing.source))).scalars().all()
    last_run = (
        await session.execute(
            select(CollectorRun).order_by(CollectorRun.started_at.desc()).limit(1)
        )
    ).scalar_one_or_none()

    map_points = _map_points(
        map_listings, map_user_states, await _apartment_summaries(session, map_listings)
    )
    return templates.TemplateResponse(
        request,
        "listings.html",
        {
            "filters": filters,
            **row_context,
            "total": total or 0,
            "limit": PAGE_SIZE,
            "districts": districts,
            "sources": sources,
            "last_run": last_run,
            "contexts": contexts,
            "selected_context": selected_context,
            "map_points": Markup(json.dumps(map_points, ensure_ascii=False)),
            "map_points_count": len(map_points),
            "map_source_count": len(map_listings),
            "map_points_limit": MAP_POINTS_LIMIT,
            "map_context": Markup(json.dumps(_map_context(selected_context), ensure_ascii=False)),
            "sort_url": _sort_url,
            "view_url": _view_url,
            "context_url": _context_url,
            "current_user": user,
        },
    )


@app.post("/api/listings/spatial")
async def spatial_listings(
    request: Request,
    payload: SpatialFilterPayload,
    filters: ListingFilters = FILTERS_DEP,
    session: AsyncSession = SESSION_DEP,
    user: User = USER_DEP,
) -> dict[str, object]:
    contexts = await _contexts(session, user)
    if not contexts:
        return {"total": 0, "shown": 0, "limit": PAGE_SIZE, "listing_ids": [], "rows_html": ""}
    selected_context = _selected_context(contexts, filters.context)
    filters.context = selected_context.slug
    stmt = _spatial_listings_query(
        _filtered_listings_query(filters, selected_context, user), payload
    )
    candidates = list((await session.execute(stmt)).scalars().all())
    candidates = [
        item for item in candidates if in_context(item, selected_context, filters.location)
    ]
    if payload.mode == "polygon":
        candidates = [item for item in candidates if _listing_in_polygon(item, payload.polygon)]
    candidates = unique_apartments(candidates)
    total = len(candidates)
    row_context = await _table_rows_context(session, candidates, filters, selected_context, user)
    rows_html = templates.get_template("_listing_rows.html").render(
        request=request,
        filters=filters,
        current_user=user,
        **row_context,
    )
    return {
        "total": total,
        "shown": len(row_context["listings"]),
        "limit": PAGE_SIZE,
        "listing_ids": [str(item.group_id or item.id) for item in candidates],
        "rows_html": rows_html,
    }


@app.get("/runs", response_class=HTMLResponse)
async def collector_runs_page(
    request: Request,
    session: AsyncSession = SESSION_DEP,
    _admin: User = ADMIN_DEP,
) -> HTMLResponse:
    rows = (
        await session.execute(
            select(CollectorRun, Search)
            .outerjoin(Search, Search.id == CollectorRun.search_id)
            .order_by(CollectorRun.started_at.desc())
            .limit(200)
        )
    ).all()
    runs: list[CollectorRunView] = [
        {
            "run": run,
            "search_name": search.name if search is not None else "-",
            "duration_label": _run_duration(run),
        }
        for run, search in rows
    ]
    settings = Settings()
    policy = AvitoPolicy(
        settings.avito_policy_path.resolve(), daily_pages=settings.avito_daily_pages
    )
    policy_state = policy.snapshot()
    return templates.TemplateResponse(
        request,
        "collector_runs.html",
        {
            "runs": runs,
            "avito_policy": policy_state,
            "avito_pause": policy.reason(policy_state, time.time()),
            "avito_budget": settings.avito_daily_pages,
        },
    )


@app.post("/runs/avito/probe")
async def avito_probe(request: Request, _admin: User = ADMIN_DEP) -> RedirectResponse:
    if request.headers.get("origin") != str(request.base_url).rstrip("/"):
        raise HTTPException(status_code=403, detail="Same-origin request required")
    AvitoPolicy(Settings().avito_policy_path.resolve()).request_probe()
    return RedirectResponse("/runs", status_code=303)


@app.get("/listings/{listing_id}", response_class=HTMLResponse)
async def listing_detail(
    request: Request,
    listing_id: UUID,
    session: AsyncSession = SESSION_DEP,
    user: User = USER_DEP,
) -> HTMLResponse:
    listing = await session.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    if not await _listing_visible_to_user(session, listing, user):
        raise HTTPException(status_code=404, detail="Listing not found")
    observations = (
        (
            await session.execute(
                select(ListingObservation)
                .where(ListingObservation.listing_id == listing.id)
                .order_by(ListingObservation.observed_at.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    stats = await listing_history_stats(session, [listing.id])
    segments = build_market_segments(await _recent_market_listings(session, 180, None, user))
    listing_stats = stats.get(listing.id, ListingHistoryStats())
    recommendation = recommend_listing(
        listing,
        listing_stats,
        segments.get(segment_key(listing)),
    )
    price_changes = (
        (
            await session.execute(
                select(PriceHistory)
                .where(PriceHistory.listing_id == listing.id)
                .order_by(PriceHistory.observed_at.desc())
            )
        )
        .scalars()
        .all()
    )

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
            "user_state": await _user_state(session, listing.id, user),
            "current_user": user,
        },
    )


@app.get("/contexts/new", response_class=HTMLResponse)
async def new_context_page(request: Request, user: User = USER_DEP) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "context_form.html",
        {
            "sources": SOURCE_CHOICES,
            "defaults": {
                "object_type": "flat",
                "city": "Самара",
                "radius_km": "50",
            },
            "current_user": user,
        },
    )


@app.post("/contexts")
async def create_context(
    request: Request,
    session: AsyncSession = SESSION_DEP,
    user: User = USER_DEP,
) -> RedirectResponse:
    body = (await request.body()).decode()
    form = parse_qs(body, keep_blank_values=True)
    name = _form_value(form, "name")
    object_type = _form_value(form, "object_type") or "flat"
    city = _form_value(form, "city") or "Самара"
    rooms_raw = _form_value(form, "expected_rooms")
    radius_raw = _form_value(form, "radius_km")
    sources = form.get("sources", [])
    rules = _context_rules_from_form(form)
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    if object_type not in {"flat", "land"}:
        raise HTTPException(status_code=422, detail="object_type must be flat or land")
    expected_rooms = int(rooms_raw) if rooms_raw else None
    radius_km = float(radius_raw.replace(",", ".")) if radius_raw else None
    slug = await _unique_context_slug(session, _slugify(name))
    context = (
        await session.execute(select(SearchContext).where(SearchContext.slug == slug))
    ).scalar_one_or_none()
    if context is not None:
        raise HTTPException(status_code=409, detail="context already exists")
    context = SearchContext(
        slug=slug,
        name=name,
        owner_user_id=user.id,
        object_type=object_type,
        city=city,
        expected_rooms=expected_rooms,
        center_latitude=53.195873,
        center_longitude=50.100193,
        radius_km=radius_km,
        enabled=True,
        rules=rules,
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
    user: User = USER_DEP,
) -> RedirectResponse:
    listing = await session.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    if not await _listing_visible_to_user(session, listing, user):
        raise HTTPException(status_code=404, detail="Listing not found")

    if listing.group_id:
        group_state = await _apartment_user_state(session, listing.group_id, user)
        if group_state is None:
            group_state = ApartmentUserState(user_id=user.id, group_id=listing.group_id)
            session.add(group_state)
        if action in {"favorite", "unfavorite"}:
            group_state.is_favorite = action == "favorite"
        else:
            group_state.is_hidden = action == "hide"
        await session.commit()
        return RedirectResponse(_local_return(request), status_code=303)
    state = await _user_state(session, listing_id, user)
    if state is None:
        state = ListingUserState(user_id=user.id, listing_id=listing_id)
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
    return RedirectResponse(_local_return(request), status_code=303)


def _filtered_listings_query(
    filters: ListingFilters,
    context: SearchContext,
    user: User | None = None,
) -> Select[tuple[Listing]]:
    conditions = [
        Listing.is_active.is_(True),
        Listing.last_seen_at >= datetime.now(UTC) - timedelta(days=filters.seen_days),
        Search.context_id == context.id,
    ]
    if context.expected_rooms is not None:
        conditions.append(Listing.rooms == context.expected_rooms)
    rules = context.rules or {}
    if filters.price_min is None and (price_min := _optional_rule_int(rules, "price_min")):
        conditions.append(Listing.price_rub >= price_min)
    if filters.price_max is None and (price_max := _optional_rule_int(rules, "price_max")):
        conditions.append(Listing.price_rub <= price_max)
    if filters.price_m2_max is None and (price_m2_max := _optional_rule_int(rules, "price_m2_max")):
        conditions.append(Listing.price_per_m2 <= price_m2_max)
    if filters.area_min is None and (area_min := _optional_rule_float(rules, "area_min")):
        conditions.append(Listing.area_total_m2 >= area_min)
    if filters.area_max is None and (area_max := _optional_rule_float(rules, "area_max")):
        conditions.append(Listing.area_total_m2 <= area_max)
    if filters.floor_min is None and (floor_min := _optional_rule_int(rules, "floor_min")):
        conditions.append(Listing.floor >= floor_min)
    if filters.floor_max is None and (floor_max := _optional_rule_int(rules, "floor_max")):
        conditions.append(Listing.floor <= floor_max)
    if filters.floors_total_max is None and (
        floors_total_max := _optional_rule_int(rules, "floors_total_max")
    ):
        conditions.append(Listing.floors_total <= floors_total_max)
    if not filters.district and (district := _optional_rule_text(rules, "district")):
        conditions.append(Listing.district == district)
    if filters.q:
        text_queries = {
            filters.q,
            filters.q.lower(),
            filters.q.upper(),
            filters.q.capitalize(),
        }
        columns = (
            Listing.title,
            Listing.description,
            Listing.address_raw,
            Listing.address_normalized,
            Listing.source_listing_id,
            Listing.url,
        )
        conditions.append(
            or_(
                *[column.like(f"%{query}%") for column in columns for query in text_queries],
            )
        )
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

    favorite = func.coalesce(ApartmentUserState.is_favorite, ListingUserState.is_favorite, False)
    hidden = func.coalesce(ApartmentUserState.is_hidden, ListingUserState.is_hidden, False)
    match filters.view:
        case "favorites":
            conditions.extend([favorite.is_(True), hidden.is_(False)])
        case "hidden":
            conditions.append(hidden.is_(True))
        case _:
            conditions.append(hidden.is_(False))

    stmt = (
        select(Listing)
        .join(ListingObservation, ListingObservation.listing_id == Listing.id)
        .join(Search, ListingObservation.search_id == Search.id)
        .outerjoin(
            ListingUserState,
            and_(
                ListingUserState.listing_id == Listing.id,
                ListingUserState.user_id == user.id if user else true(),
            ),
        )
        .outerjoin(
            ApartmentUserState,
            and_(
                ApartmentUserState.group_id == Listing.group_id,
                ApartmentUserState.user_id == user.id if user else true(),
            ),
        )
        .options(defer(Listing.raw_payload))
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


async def _table_rows_context(
    session: AsyncSession,
    candidates: list[Listing],
    filters: ListingFilters,
    context: SearchContext,
    user: User | None = None,
) -> TableRowsContext:
    candidates[:] = unique_apartments(candidates)
    candidate_ids = [item.id for item in candidates]
    candidate_stats = await listing_history_stats(session, candidate_ids)
    segments = build_market_segments(
        await _recent_market_listings(session, filters.seen_days, context, user)
    )
    candidate_recommendations = {
        item.id: recommend_listing(
            item,
            candidate_stats[item.id],
            segments.get(segment_key(item)),
        )
        for item in candidates
    }
    if filters.sort in {"best", "score_asc"}:
        candidates.sort(
            key=lambda item: candidate_recommendations[item.id].score,
            reverse=filters.sort == "best",
        )
    listings = candidates[:PAGE_SIZE]
    return {
        "listings": listings,
        "stats": {item.id: candidate_stats[item.id] for item in listings},
        "recommendations": {item.id: candidate_recommendations[item.id] for item in listings},
        "user_states": await _user_states(session, [item.id for item in listings], user),
        "apartments": await _apartment_summaries(session, listings),
    }


def _spatial_listings_query(
    stmt: Select[tuple[Listing]],
    payload: SpatialFilterPayload,
) -> Select[tuple[Listing]]:
    conditions: list[ColumnElement[bool]] = [
        Listing.latitude.is_not(None),
        Listing.longitude.is_not(None),
    ]
    if payload.mode == "bounds":
        if (
            payload.north is None
            or payload.south is None
            or payload.east is None
            or payload.west is None
        ):
            raise HTTPException(status_code=422, detail="bounds coordinates are required")
        north = max(payload.north, payload.south)
        south = min(payload.north, payload.south)
        east = max(payload.east, payload.west)
        west = min(payload.east, payload.west)
    else:
        if len(payload.polygon) < 3:
            raise HTTPException(status_code=422, detail="polygon requires at least 3 points")
        latitudes = [point[0] for point in payload.polygon]
        longitudes = [point[1] for point in payload.polygon]
        north = max(latitudes)
        south = min(latitudes)
        east = max(longitudes)
        west = min(longitudes)
    conditions.extend(
        [
            Listing.latitude <= north,
            Listing.latitude >= south,
            Listing.longitude <= east,
            Listing.longitude >= west,
        ]
    )
    return stmt.where(and_(*conditions))


def _listing_in_polygon(listing: Listing, polygon: list[tuple[float, float]]) -> bool:
    if listing.latitude is None or listing.longitude is None:
        return False
    return _point_in_polygon(listing.latitude, listing.longitude, polygon)


def _point_in_bounds(
    latitude: float,
    longitude: float,
    *,
    north: float,
    south: float,
    east: float,
    west: float,
) -> bool:
    return south <= latitude <= north and west <= longitude <= east


def _point_in_polygon(
    latitude: float,
    longitude: float,
    polygon: list[tuple[float, float]],
) -> bool:
    inside = False
    j = len(polygon) - 1
    for i, point in enumerate(polygon):
        lat_i, lng_i = point
        lat_j, lng_j = polygon[j]
        intersects = (lng_i > longitude) != (lng_j > longitude)
        if intersects:
            lat_at_lng = (lat_j - lat_i) * (longitude - lng_i) / (lng_j - lng_i) + lat_i
            if latitude <= lat_at_lng:
                inside = not inside
        j = i
    return inside


def _listing_in_context(context: SearchContext):
    return (
        exists()
        .where(ListingObservation.listing_id == Listing.id)
        .where(ListingObservation.search_id == Search.id)
        .where(Search.context_id == context.id)
    )


def _distinct_values(column) -> Select[tuple[str]]:
    return select(column).where(column.is_not(None)).distinct().order_by(column)


async def _contexts(session: AsyncSession, user: User | None = None) -> list[SearchContext]:
    stmt = select(SearchContext).where(SearchContext.enabled.is_(True))
    if user is not None and user.role != "admin":
        stmt = stmt.where(SearchContext.owner_user_id == user.id)
    return list(
        (
            await session.execute(stmt.order_by(SearchContext.created_at, SearchContext.name))
        ).scalars()
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


def _run_duration(run: CollectorRun) -> str:
    if run.finished_at is None:
        return "идет"
    seconds = max(int((run.finished_at - run.started_at).total_seconds()), 0)
    minutes, rest_seconds = divmod(seconds, 60)
    hours, rest_minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} ч {rest_minutes} мин"
    if minutes:
        return f"{minutes} мин {rest_seconds} сек"
    return f"{rest_seconds} сек"


def _map_points(
    listings: list[Listing],
    user_states: dict[UUID, ListingUserState],
    apartments: dict[UUID, dict] | None = None,
) -> list[dict[str, object]]:
    points = []
    for item in listings:
        apartment = (apartments or {}).get(item.id, {})
        latitude = apartment.get("latitude", item.latitude)
        longitude = apartment.get("longitude", item.longitude)
        if not usable_coordinates(latitude, longitude):
            continue
        points.append(
            {
                "id": str(item.group_id or item.id),
                "listing_id": str(item.id),
                "lat": latitude,
                "lng": longitude,
                "price": apartment.get("price", format_rub(item.price_rub)),
                "title": item.address_normalized or item.address_raw or item.title or "-",
                "source": apartment.get("source_label", item.source),
                "url": f"/apartments/{item.group_id}" if item.group_id else f"/listings/{item.id}",
                "favorite_action": "unfavorite"
                if user_states.get(item.id) and user_states[item.id].is_favorite
                else "favorite",
                "favorite_label": "Убрать из избранного"
                if user_states.get(item.id) and user_states[item.id].is_favorite
                else "В избранное",
            }
        )
    return points


def _map_context(context: SearchContext) -> dict[str, object]:
    return {
        "lat": context.center_latitude or 53.195873,
        "lng": context.center_longitude or 50.100193,
        "radiusKm": context.radius_km,
        "objectType": context.object_type,
    }


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


def _context_rules_from_form(form: dict[str, list[str]]) -> dict[str, object]:
    rules: dict[str, object] = {"created_from_ui": True}
    for name in (
        "price_min",
        "price_max",
        "price_m2_max",
        "area_min",
        "area_max",
        "floor_min",
        "floor_max",
        "floors_total_max",
        "district",
    ):
        value = _form_value(form, name)
        if value:
            rules[name] = value
    return rules


async def _unique_context_slug(session: AsyncSession, base_slug: str) -> str:
    slug = base_slug
    index = 2
    while (
        await session.execute(select(SearchContext.id).where(SearchContext.slug == slug))
    ).scalar_one_or_none():
        slug = f"{base_slug}_{index}"
        index += 1
    return slug


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
            "avito": "https://www.avito.ru/samara/zemelnye_uchastki",
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
    if source == "domclick":
        return (
            "https://samara.domclick.ru/search?deal_type=sale&category=living"
            f"&offer_type=flat&rooms={rooms}&address=6369cbfc-1f06-4574-adba-82f4dc42c0f7"
        )
    if rooms == 3:
        urls = {
            "avito": "https://www.avito.ru/samara/kvartiry/prodam/3-komnatnye-ASgBAgICAUSSA8YQAkDmBxSM",
            "etagi": "https://samara.etagi.com/realty/trehkomnatnye-kvartiry/",
            "mirkvartir": "https://www.mirkvartir.ru/Самарская+область/Самара/Трехкомнатные/",
            "n1": "https://samara-1.n1.ru/kupit/kvartiry/rooms-trehkomnatnye/",
            "yandex_realty": "https://realty.yandex.ru/samara/kupit/kvartira/tryohkomnatnaya/",
        }
        return urls.get(source)
    return None


async def _user_state(
    session: AsyncSession, listing_id: UUID, user: User | None = None
) -> ListingUserState | None:
    return (await _user_states(session, [listing_id], user)).get(listing_id)


async def _user_states(
    session: AsyncSession,
    listing_ids: list[UUID],
    user: User | None = None,
) -> dict[UUID, ListingUserState]:
    if not listing_ids:
        return {}
    user_filter = ListingUserState.user_id == user.id if user else true()
    rows = (
        await session.execute(
            select(ListingUserState).where(
                ListingUserState.listing_id.in_(listing_ids), user_filter
            )
        )
    ).scalars()
    result = {state.listing_id: state for state in rows}
    group_user_filter = ApartmentUserState.user_id == user.id if user else true()
    groups = (
        await session.execute(
            select(Listing.id, ApartmentUserState)
            .join(ApartmentUserState, ApartmentUserState.group_id == Listing.group_id)
            .where(Listing.id.in_(listing_ids))
            .where(group_user_filter)
        )
    ).all()
    for listing_id, group_state in groups:
        result[listing_id] = ListingUserState(
            user_id=user.id if user else None,
            listing_id=listing_id,
            is_favorite=group_state.is_favorite,
            is_hidden=group_state.is_hidden,
        )
    return result


async def _apartment_user_state(
    session: AsyncSession, group_id: UUID, user: User
) -> ApartmentUserState | None:
    return (
        await session.execute(
            select(ApartmentUserState).where(
                ApartmentUserState.group_id == group_id,
                ApartmentUserState.user_id == user.id,
            )
        )
    ).scalar_one_or_none()


async def _listing_visible_to_user(session: AsyncSession, listing: Listing, user: User) -> bool:
    if user.role == "admin":
        return True
    return bool(
        await session.scalar(
            select(func.count())
            .select_from(ListingObservation)
            .join(Search, ListingObservation.search_id == Search.id)
            .join(SearchContext, Search.context_id == SearchContext.id)
            .where(
                ListingObservation.listing_id == listing.id,
                SearchContext.owner_user_id == user.id,
            )
        )
    )


async def _recent_market_listings(
    session: AsyncSession,
    seen_days: int,
    context: SearchContext | None = None,
    user: User | None = None,
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
    if context is None and user is not None and user.role != "admin":
        conditions.append(SearchContext.owner_user_id == user.id)
    market_listings = unique_apartments(
        list(
            (
                await session.execute(
                    select(Listing)
                    .join(ListingObservation, ListingObservation.listing_id == Listing.id)
                    .join(Search, ListingObservation.search_id == Search.id)
                    .outerjoin(SearchContext, Search.context_id == SearchContext.id)
                    .where(*conditions)
                    .options(defer(Listing.raw_payload))
                    .order_by(Listing.price_rub.asc().nulls_last(), Listing.id)
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
    )
    return [item for item in market_listings if context is None or in_context(item, context)]


def unique_apartments(listings: list[Listing]) -> list[Listing]:
    seen: set[UUID] = set()
    result = []
    for item in listings:
        key = item.group_id or item.id
        if key not in seen:
            result.append(item)
            seen.add(key)
    return result


def _range_label(values, formatter) -> str:
    numbers = sorted({value for value in values if value is not None})
    if not numbers:
        return "-"
    if numbers[0] == numbers[-1]:
        return formatter(numbers[0])
    return f"{formatter(numbers[0])} – {formatter(numbers[-1])}"


async def _apartment_summaries(session: AsyncSession, listings: list[Listing]) -> dict[UUID, dict]:
    group_ids = {item.group_id for item in listings if item.group_id}
    members: dict[UUID, list[Listing]] = {}
    if group_ids:
        rows = await session.scalars(
            select(Listing)
            .where(Listing.group_id.in_(group_ids))
            .options(defer(Listing.raw_payload))
        )
        for row in rows:
            assert row.group_id is not None
            members.setdefault(row.group_id, []).append(row)
    result = {}
    for item in listings:
        group = members.get(item.group_id, [item]) if item.group_id else [item]
        coordinates = next(
            (
                member
                for member in [item, *group]
                if usable_coordinates(member.latitude, member.longitude)
            ),
            item,
        )
        sources = sorted({member.source for member in group})
        result[item.id] = {
            "price": _range_label(
                [member.price_rub for member in group if member.is_active], format_rub
            ),
            "area": _range_label(
                [member.area_total_m2 for member in group],
                lambda value: f"{float(value):g}".replace(".", ","),
            )
            + " м²",
            "source_label": ", ".join(sources),
            "source_count": len(sources),
            "url": f"/apartments/{item.group_id}" if item.group_id else f"/listings/{item.id}",
            "latitude": coordinates.latitude,
            "longitude": coordinates.longitude,
        }
    return result


def _local_return(request: Request) -> str:
    from urllib.parse import urlsplit

    referer = urlsplit(request.headers.get("referer", "/"))
    if referer.netloc and referer.netloc != request.url.netloc:
        return "/"
    return (referer.path or "/") + (f"?{referer.query}" if referer.query else "")


class MergeSelection(BaseModel):
    left: UUID
    right: UUID
    fingerprint: str | None = None
    acknowledge: bool = False


async def _merge_preview(session: AsyncSession, left: UUID, right: UUID) -> dict:
    if left == right:
        raise HTTPException(409, "Выбрана одна и та же квартира")
    groups = []
    members = []
    for ident in (left, right):
        group = await session.get(ApartmentGroup, ident)
        items = await group_members(session, ident)
        if not group or not items:
            raise HTTPException(
                409, "Состав квартиры изменился. Обновите список и выберите её заново."
            )
        members.append(items)
        groups.append(
            {
                "id": str(ident),
                "needs_review": group.needs_review,
                "items": [
                    {
                        "id": str(item.id),
                        "source": item.source,
                        "address": item.address_normalized
                        or item.address_raw
                        or "Адрес неизвестен",
                        "rooms": item.rooms,
                        "area": str(item.area_total_m2 or "—"),
                        "floor": item.floor,
                        "floors": item.floors_total,
                        "price": item.price_rub,
                    }
                    for item in items
                ],
            }
        )
    warnings = set()
    for a, b in itertools.product(*members):
        for field, label in (
            ("rooms", "Комнатность"),
            ("floor", "Этаж"),
            ("floors_total", "Этажность дома"),
        ):
            if (
                getattr(a, field) is not None
                and getattr(b, field) is not None
                and getattr(a, field) != getattr(b, field)
            ):
                warnings.add(f"{label} не совпадает")
        ka, kb = building_key(a), building_key(b)
        if not ka or not kb:
            warnings.add("Не удалось подтвердить адрес дома")
        elif ka != kb:
            warnings.add("Адреса домов не совпадают")
        if a.source == b.source:
            warnings.add("В группе будут несколько объявлений одного источника")
    ids = [item.id for items in members for item in items]
    rejected = (
        await session.scalars(
            select(ListingLink).where(
                ListingLink.listing_id_a.in_(ids),
                ListingLink.listing_id_b.in_(ids),
                ListingLink.match_type == "apartment",
                ListingLink.status == "rejected",
            )
        )
    ).all()
    if rejected:
        warnings.add("Эти объявления ранее разделяли или отклоняли их объединение")
    if any(group["needs_review"] for group in groups):
        warnings.add("Группа уже отмечена для проверки")
    result = {"groups": groups, "warnings": sorted(warnings)}
    result["fingerprint"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


@app.post("/api/apartments/merge-preview")
async def apartment_merge_preview(
    payload: MergeSelection,
    session: AsyncSession = SESSION_DEP,
    _admin: User = ADMIN_DEP,
) -> dict:
    return await _merge_preview(session, payload.left, payload.right)


@app.post("/api/apartments/merge")
async def apartment_merge(
    request: Request,
    payload: MergeSelection,
    session: AsyncSession = SESSION_DEP,
    _admin: User = ADMIN_DEP,
) -> dict:
    if request.headers.get("origin") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Same-origin request required")
    # Serialize membership changes before rechecking the preview (also on SQLite).
    await session.execute(
        update(ApartmentGroup)
        .where(ApartmentGroup.id.in_([payload.left, payload.right]))
        .values(needs_review=ApartmentGroup.needs_review)
    )
    preview = await _merge_preview(session, payload.left, payload.right)
    if payload.fingerprint != preview["fingerprint"]:
        raise HTTPException(409, "Данные изменились. Сравните карточки ещё раз.")
    if preview["warnings"] and not payload.acknowledge:
        raise HTTPException(409, "Подтвердите предупреждения перед объединением")
    a = (await group_members(session, payload.left))[0]
    b = (await group_members(session, payload.right))[0]
    link = await set_link(session, a, b, "confirmed", "manual")
    group_id = await confirm_link(session, link)
    await session.commit()
    return {"group_id": str(group_id), "url": f"/apartments/{group_id}"}


@app.get("/apartments/{group_id}", response_class=HTMLResponse)
async def apartment_detail(
    request: Request,
    group_id: UUID,
    session: AsyncSession = SESSION_DEP,
    user: User = USER_DEP,
) -> HTMLResponse:
    group = await session.get(ApartmentGroup, group_id)
    members = await group_members(session, group_id)
    if group is None or not members:
        raise HTTPException(404, "Apartment not found")
    if user.role != "admin" and not any(
        [await _listing_visible_to_user(session, item, user) for item in members]
    ):
        raise HTTPException(404, "Apartment not found")
    histories: dict[UUID, list[PriceHistory]] = {item.id: [] for item in members}
    for change in (
        await session.scalars(
            select(PriceHistory)
            .where(PriceHistory.listing_id.in_(histories))
            .order_by(PriceHistory.observed_at.desc())
        )
    ).all():
        histories[change.listing_id].append(change)
    return templates.TemplateResponse(
        request,
        "apartment_detail.html",
        {
            "group": group,
            "members": members,
            "histories": histories,
            "summary": (await _apartment_summaries(session, [members[0]]))[members[0].id],
            "user_state": await _apartment_user_state(session, group_id, user),
            "current_user": user,
        },
    )


@app.post("/apartments/{group_id}/split")
async def apartment_split(
    group_id: UUID,
    listing_id: UUID,
    session: AsyncSession = SESSION_DEP,
    _admin: User = ADMIN_DEP,
) -> RedirectResponse:
    try:
        new_id = await split_member(session, group_id, listing_id)
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(f"/apartments/{new_id}", 303)


@app.get("/duplicates", response_class=HTMLResponse)
async def duplicates_page(
    request: Request,
    status: str = Query("candidate", pattern="^(candidate|rejected)$"),
    group_id: UUID | None = None,
    page: int = Query(1, ge=1),
    session: AsyncSession = SESSION_DEP,
    _admin: User = ADMIN_DEP,
) -> HTMLResponse:
    a, b = aliased(Listing), aliased(Listing)
    pairs = (
        await session.execute(
            select(ListingLink, a, b)
            .join(a, a.id == ListingLink.listing_id_a)
            .join(b, b.id == ListingLink.listing_id_b)
            .where(
                ListingLink.match_type == "apartment",
                ListingLink.status == status,
                a.group_id != b.group_id,
                or_(a.group_id == group_id, b.group_id == group_id) if group_id else true(),
            )
            .order_by(ListingLink.updated_at.desc(), ListingLink.id)
            .limit(200)
            .offset((page - 1) * 200)
        )
    ).all()
    reviews = (
        await session.scalars(select(ApartmentGroup).where(ApartmentGroup.needs_review.is_(True)))
    ).all()
    return templates.TemplateResponse(
        request,
        "duplicates.html",
        {
            "pairs": pairs,
            "status": status,
            "reviews": reviews,
            "group_id": group_id,
            "page": page,
        },
    )


@app.post("/duplicates/{link_id}")
async def duplicate_decision(
    link_id: UUID,
    action: str = Query(pattern="^(confirm|reject)$"),
    session: AsyncSession = SESSION_DEP,
    _admin: User = ADMIN_DEP,
) -> RedirectResponse:
    link = await session.get(ListingLink, link_id)
    if link is None:
        raise HTTPException(404, "Candidate not found")
    try:
        if action == "confirm":
            group_id = await confirm_link(session, link)
            destination = f"/apartments/{group_id}"
        else:
            a = await session.get(Listing, link.listing_id_a)
            b = await session.get(Listing, link.listing_id_b)
            if a and b and a.group_id == b.group_id:
                raise ValueError("Split a member before rejecting an existing group")
            link.status = "rejected"
            link.decision_origin = "manual"
            destination = "/duplicates"
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(destination, 303)
