import asyncio
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select
from test_ai_recommendations import _listing
from test_ai_recommendations import factory as factory
from test_ai_recommendations import override_db as override_db

from app.models import ApartmentGroup, ListingObservation, PriceHistory, Search, SearchContext, User
from app.web import app
from services.auth import SESSION_COOKIE_NAME, create_user_session, hash_password


async def test_group_cannot_leak_other_users_sources_history_or_map(factory, override_db):
    async with factory() as session:
        owner = User(username="owner", display_name="Owner", password_hash=hash_password("secret"))
        other = User(username="other", display_name="Other", password_hash=hash_password("secret"))
        admin = User(
            username="admin",
            display_name="Admin",
            role="admin",
            password_hash=hash_password("secret"),
        )
        group = ApartmentGroup()
        session.add_all([owner, other, admin, group])
        await session.flush()
        contexts = [
            SearchContext(
                slug=u.username, name=u.display_name, owner_user_id=u.id, expected_rooms=3
            )
            for u in (owner, other)
        ]
        session.add_all(contexts)
        await session.flush()
        searches = [
            Search(
                name=c.slug,
                context_id=c.id,
                source="cian",
                url="https://example.test",
                city="Самара",
                rooms=3,
            )
            for c in contexts
        ]
        visible = _listing(
            source_listing_id="visible", group_id=group.id, last_seen_at=datetime.now(UTC)
        )
        private = _listing(
            source_listing_id="private",
            source="etagi",
            group_id=group.id,
            title="PRIVATE_TITLE",
            description="PRIVATE_DESCRIPTION",
            url="https://example.test/PRIVATE_URL",
            district="PRIVATE_DISTRICT",
            latitude=53.21,
            longitude=50.12,
            price_rub=123,
            last_seen_at=datetime.now(UTC),
        )
        session.add_all([*searches, visible, private])
        await session.flush()
        session.add_all(
            [
                ListingObservation(listing_id=visible.id, search_id=searches[0].id),
                ListingObservation(listing_id=private.id, search_id=searches[1].id),
                PriceHistory(listing_id=private.id, new_price_rub=456),
            ]
        )
        _, token = await create_user_session(session, owner, days=1)
        _, admin_token = await create_user_session(session, admin, days=1)
        group_id, private_id = group.id, private.id
        await session.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        for url in ("/?context=owner", f"/apartments/{group_id}"):
            page = await client.get(url)
            assert page.status_code == 200
            for text in (
                "PRIVATE_TITLE",
                "PRIVATE_DESCRIPTION",
                "PRIVATE_URL",
                "PRIVATE_DISTRICT",
                "etagi",
                str(private_id),
            ):
                assert text not in page.text
            assert "Проверка дублей" not in page.text
        assert (await client.get(f"/listings/{private_id}")).status_code == 404
        assert (
            await client.post(f"/listings/{private_id}/state?action=favorite")
        ).status_code == 404
        assert (await client.get("/duplicates")).status_code == 403
        client.cookies.set(SESSION_COOKIE_NAME, admin_token)
        admin_page = await client.get(f"/apartments/{group_id}")
        assert "PRIVATE_DESCRIPTION" in admin_page.text
        assert "PRIVATE_URL" in admin_page.text


async def test_subscription_capacity_is_admin_managed_and_atomic(factory, override_db):
    async with factory() as session:
        customer = User(
            username="customer", display_name="Customer", password_hash=hash_password("secret")
        )
        admin = User(
            username="admin",
            display_name="Admin",
            role="admin",
            password_hash=hash_password("secret"),
        )
        session.add_all([customer, admin])
        await session.flush()
        assert customer.context_limit == 1
        user_id = customer.id
        _, token = await create_user_session(session, customer, days=1)
        _, admin_token = await create_user_session(session, admin, days=1)
        await session.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        responses = await asyncio.gather(
            *[
                client.post(
                    "/contexts",
                    data={"name": f"Search {i}", "object_type": "flat", "expected_rooms": "3"},
                )
                for i in range(2)
            ]
        )
        assert sorted(r.status_code for r in responses) == [303, 409]
        full = await client.get("/contexts/new")
        assert "Лимит контекстов достигнут" in full.text
        assert (
            await client.post(f"/admin/users/{user_id}/context-limit", data={"context_limit": "2"})
        ).status_code == 403
        client.cookies.set(SESSION_COOKIE_NAME, admin_token)
        assert (
            await client.post(f"/admin/users/{user_id}/context-limit", data={"context_limit": "2"})
        ).status_code == 303
        client.cookies.set(SESSION_COOKIE_NAME, token)
        assert (
            await client.post(
                "/contexts", data={"name": "Extra", "object_type": "flat", "expected_rooms": "3"}
            )
        ).status_code == 303
        client.cookies.set(SESSION_COOKIE_NAME, admin_token)
        denied = await client.post(
            f"/admin/users/{user_id}/context-limit", data={"context_limit": "1"}
        )
        assert denied.status_code == 422
        assert "Нельзя установить лимит" in denied.text
    async with factory() as session:
        assert (await session.get(User, user_id)).context_limit == 2
        assert (
            await session.scalar(
                select(func.count())
                .select_from(SearchContext)
                .where(SearchContext.owner_user_id == user_id)
            )
            == 2
        )
