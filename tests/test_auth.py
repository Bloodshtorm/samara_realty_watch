from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import Base, Listing, ListingObservation, Search, SearchContext, User
from app.web import app, db_session
from services.auth import (
    SESSION_COOKIE_NAME,
    bootstrap_admin,
    create_user_session,
    hash_password,
    verify_password,
)


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth.sqlite3'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def override_db(factory):
    async def override():
        async with factory() as session:
            yield session

    app.dependency_overrides[db_session] = override
    try:
        yield
    finally:
        app.dependency_overrides.clear()


def test_password_hash_verification() -> None:
    password_hash = hash_password("secret")

    assert verify_password("secret", password_hash)
    assert not verify_password("wrong", password_hash)


async def test_avito_probe_requires_admin_and_same_origin(
    factory, override_db, tmp_path, monkeypatch
):
    from services.avito_policy import AvitoPolicy

    path = tmp_path / "policy.sqlite3"
    monkeypatch.setenv("AVITO_POLICY_PATH", str(path))
    policy = AvitoPolicy(path)
    policy.block("CAPTCHA")
    async with factory() as session, session.begin():
        admin = User(
            username="ops", display_name="Ops", role="admin", password_hash=hash_password("secret")
        )
        session.add(admin)
        await session.flush()
        _, token = await create_user_session(session, admin, days=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/runs/avito/probe", headers={"origin": "http://test"})
        assert response.status_code in (303, 401, 403)
        assert not policy.snapshot().get("probe_requested")
        client.cookies.set(SESSION_COOKIE_NAME, token)
        response = await client.get("/runs")
        assert response.status_code == 200
        assert "CAPTCHA" in response.text
        assert (
            await client.post("/runs/avito/probe", headers={"origin": "http://evil"})
        ).status_code == 403
        assert not policy.snapshot().get("probe_requested")
        assert (
            await client.post("/runs/avito/probe", headers={"origin": "http://test"})
        ).status_code == 303
        assert policy.snapshot()["blocked"]
        assert policy.snapshot()["probe_requested"]


async def test_radius_and_unknown_view_apply_before_table_limit(factory, override_db):
    async with factory() as session, session.begin():
        admin = User(
            username="geo", display_name="Geo", role="admin", password_hash=hash_password("secret")
        )
        context = SearchContext(
            slug="radius",
            name="Radius",
            object_type="land",
            radius_km=50,
            center_latitude=53.2,
            center_longitude=50.1,
        )
        session.add_all([admin, context])
        await session.flush()
        search = Search(
            name="geo",
            source="avito",
            url="https://example.test",
            city="Самара",
            context_id=context.id,
        )
        session.add(search)
        await session.flush()
        for name, lat in [("INSIDE", 53.25), ("OUTSIDE", 54.5), ("UNKNOWN", None)]:
            listing = Listing(
                source="avito",
                source_listing_id=name,
                title=name,
                url="https://example.test",
                canonical_url="https://example.test",
                latitude=lat,
                longitude=50.1 if lat else None,
                price_rub=500000,
                last_seen_at=datetime.now(UTC),
            )
            session.add(listing)
            await session.flush()
            session.add(ListingObservation(listing_id=listing.id, search_id=search.id))
        _, token = await create_user_session(session, admin, days=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        inside = await client.get("/?context=radius&price_max=600000&source=avito")
        assert inside.status_code == 200
        assert (
            "INSIDE" in inside.text
            and "OUTSIDE" not in inside.text
            and "UNKNOWN" not in inside.text
        )
        unknown = await client.get("/?context=radius&location=unknown")
        assert "UNKNOWN" in unknown.text and "INSIDE" not in unknown.text
        spatial = await client.post(
            "/api/listings/spatial?context=radius",
            json={"mode": "bounds", "north": 55, "south": 52, "east": 51, "west": 49},
        )
        assert spatial.status_code == 200 and spatial.json()["total"] == 1
        excluded = await client.post(
            "/api/listings/spatial?context=radius&price_max=100000",
            json={"mode": "bounds", "north": 55, "south": 52, "east": 51, "west": 49},
        )
        assert excluded.json()["total"] == 0


async def test_bootstrap_admin_assigns_existing_contexts(factory) -> None:
    async with factory() as session, session.begin():
        context = SearchContext(slug="legacy", name="Legacy")
        session.add(context)

    async with factory() as session, session.begin():
        admin = await bootstrap_admin(
            session,
            Settings(app_admin_username="admin", app_admin_password="secret"),
        )
        assert admin is not None
        assert admin.role == "admin"

    async with factory() as session:
        context = (await session.scalars(select(SearchContext))).one()
        assert context.owner_user_id == admin.id


async def test_login_logout_and_admin_guard(factory, override_db) -> None:
    async with factory() as session, session.begin():
        admin = User(
            username="admin",
            display_name="Admin",
            role="admin",
            password_hash=hash_password("secret"),
        )
        ivan = User(
            username="ivan",
            display_name="Иван",
            role="user",
            password_hash=hash_password("secret"),
        )
        session.add_all([admin, ivan])
        await session.flush()
        _, ivan_token = await create_user_session(session, ivan, days=30)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/healthz")).status_code == 200
        assert (await client.get("/")).status_code == 303
        bad = await client.post("/login", data={"username": "admin", "password": "wrong"})
        assert bad.status_code == 401
        good = await client.post("/login", data={"username": "admin", "password": "secret"})
        assert good.status_code == 303
        assert SESSION_COOKIE_NAME in client.cookies

        user_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        try:
            user_client.cookies.set(SESSION_COOKIE_NAME, ivan_token)
            assert (await user_client.get("/admin/users")).status_code == 403
        finally:
            await user_client.aclose()

        assert (await client.get("/logout")).status_code == 303
        assert SESSION_COOKIE_NAME not in client.cookies


async def test_admin_creates_user_and_resets_password(factory, override_db) -> None:
    async with factory() as session, session.begin():
        admin = User(
            username="admin",
            display_name="Admin",
            role="admin",
            password_hash=hash_password("secret"),
        )
        session.add(admin)
        await session.flush()
        _, token = await create_user_session(session, admin, days=30)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        created = await client.post(
            "/admin/users",
            data={"username": "ivan", "display_name": "Иван", "password": "one", "role": "user"},
        )
        assert created.status_code == 303

    async with factory() as session:
        ivan = (await session.scalars(select(User).where(User.username == "ivan"))).one()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        reset = await client.post(f"/admin/users/{ivan.id}/password", data={"password": "two"})
        assert reset.status_code == 303

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.post("/login", data={"username": "ivan", "password": "one"})
        ).status_code == 401
        assert (
            await client.post("/login", data={"username": "ivan", "password": "two"})
        ).status_code == 303


async def test_contexts_and_favorites_are_personal(factory, override_db) -> None:
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        admin = User(
            username="admin",
            display_name="Admin",
            role="admin",
            password_hash=hash_password("secret"),
        )
        ivan = User(
            username="ivan",
            display_name="Иван",
            role="user",
            password_hash=hash_password("secret"),
        )
        session.add_all([admin, ivan])
        await session.flush()
        _, admin_token = await create_user_session(session, admin, days=30)
        _, ivan_token = await create_user_session(session, ivan, days=30)
        admin_context = SearchContext(
            slug="admin_context", name="Admin Context", owner_user_id=admin.id
        )
        ivan_context = SearchContext(
            slug="ivan_context", name="Ivan Context", owner_user_id=ivan.id
        )
        session.add_all([admin_context, ivan_context])
        await session.flush()
        search = Search(
            context_id=ivan_context.id,
            name="ivan_search",
            source="domclick",
            url="https://example.test",
            city="Самара",
            rooms=2,
        )
        listing = Listing(
            id=uuid4(),
            source="domclick",
            source_listing_id="1",
            url="https://example.test/1",
            canonical_url="https://example.test/1",
            title="Двушка для Ивана",
            property_type="flat",
            rooms=2,
            area_total_m2=55,
            price_rub=7_000_000,
            price_per_m2=127_272,
            is_active=True,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add_all([search, listing])
        await session.flush()
        session.add(
            ListingObservation(listing_id=listing.id, search_id=search.id, price_rub=7_000_000)
        )
        await session.flush()
        listing_id = listing.id

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ivan_client:
        ivan_client.cookies.set(SESSION_COOKIE_NAME, ivan_token)
        page = await ivan_client.get("/")
        assert page.status_code == 200
        assert "Ivan Context" in page.text
        assert "Admin Context" not in page.text
        assert (
            await ivan_client.post(f"/listings/{listing_id}/state?action=favorite")
        ).status_code == 303
        assert "Найдено: 1" in (await ivan_client.get("/?view=favorites")).text

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as admin_client:
        admin_client.cookies.set(SESSION_COOKIE_NAME, admin_token)
        assert "Admin Context" in (await admin_client.get("/")).text
        assert (
            "Найдено: 0" in (await admin_client.get("/?context=ivan_context&view=favorites")).text
        )


async def test_user_creates_personal_context_with_disabled_searches(factory, override_db) -> None:
    async with factory() as session, session.begin():
        ivan = User(
            username="ivan",
            display_name="Иван",
            role="user",
            password_hash=hash_password("secret"),
        )
        session.add(ivan)
        await session.flush()
        _, token = await create_user_session(session, ivan, days=30)
        user_id = ivan.id

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        response = await client.post(
            "/contexts",
            data={
                "name": "Иван двушка",
                "object_type": "flat",
                "city": "Самара",
                "expected_rooms": "2",
                "radius_km": "30",
                "price_max": "9000000",
                "area_min": "50",
                "floor_min": "3",
                "sources": ["cian", "domclick"],
            },
        )
        assert response.status_code == 303

    async with factory() as session:
        context = (await session.scalars(select(SearchContext))).one()
        assert context.owner_user_id == user_id
        assert context.expected_rooms == 2
        assert context.rules and context.rules["price_max"] == "9000000"
        searches = (await session.scalars(select(Search))).all()
        assert {search.source for search in searches} == {"cian", "domclick"}
        assert not any(search.enabled for search in searches)


async def test_direct_merge_preview_confirmation_and_stale_state(factory, override_db):
    from app.models import ApartmentGroup, ListingLink, PriceHistory
    from services.apartments import set_link

    async with factory() as session, session.begin():
        admin = User(
            username="merge-admin",
            display_name="Admin",
            role="admin",
            password_hash=hash_password("secret"),
        )
        reader = User(
            username="merge-reader",
            display_name="Reader",
            role="user",
            password_hash=hash_password("secret"),
        )
        groups = [ApartmentGroup(), ApartmentGroup()]
        session.add_all([admin, reader, *groups])
        await session.flush()
        _, token = await create_user_session(session, admin, days=1)
        _, reader_token = await create_user_session(session, reader, days=1)
        items = [
            Listing(
                source=source,
                source_listing_id=str(index),
                group_id=groups[index].id,
                url="https://example.test",
                canonical_url="https://example.test",
                property_type="flat",
                rooms=3,
                floor=5,
                floors_total=9,
                address_raw="Самара, Липяговская, 9",
                area_total_m2=66 + index / 10,
                price_rub=3500000,
            )
            for index, source in enumerate(("cian", "etagi"))
        ]
        session.add_all(items)
        await session.flush()
        session.add(
            PriceHistory(listing_id=items[0].id, old_price_rub=3600000, new_price_rub=3500000)
        )
        await set_link(session, *items, "rejected", "manual")
        payload = {"left": str(groups[0].id), "right": str(groups[1].id)}
        item_ids = [item.id for item in items]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.cookies.set(SESSION_COOKIE_NAME, reader_token)
        assert (await client.post("/api/apartments/merge-preview", json=payload)).status_code == 403
        client.cookies.set(SESSION_COOKIE_NAME, token)
        preview = await client.post("/api/apartments/merge-preview", json=payload)
        assert preview.status_code == 200
        assert preview.json()["warnings"]
        payload["fingerprint"] = preview.json()["fingerprint"]
        assert (await client.post("/api/apartments/merge", json=payload)).status_code == 403
        client.headers["origin"] = "http://test"
        assert (await client.post("/api/apartments/merge", json=payload)).status_code == 409
        payload["acknowledge"] = True
        saved = payload["fingerprint"]
        payload["fingerprint"] = "stale"
        assert (await client.post("/api/apartments/merge", json=payload)).status_code == 409
        payload["fingerprint"] = saved
        result = await client.post("/api/apartments/merge", json=payload)
        assert result.status_code == 200
        assert (await client.post("/api/apartments/merge", json=payload)).status_code == 409
    async with factory() as session:
        rows = (await session.scalars(select(Listing).where(Listing.id.in_(item_ids)))).all()
        assert len(rows) == 2
        assert rows[0].group_id == rows[1].group_id
        assert len((await session.scalars(select(PriceHistory))).all()) == 1
        link = (await session.scalars(select(ListingLink))).one()
        assert link.status == "confirmed" and link.decision_origin == "manual"
