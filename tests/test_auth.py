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
            ListingObservation(
                listing_id=listing.id, search_id=search.id, price_rub=7_000_000
            )
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
        assert "Найдено: 0" in (
            await admin_client.get("/?context=ivan_context&view=favorites")
        ).text


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
