from __future__ import annotations

import gzip
import importlib
import uuid
from datetime import UTC, datetime

import httpx
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from bs4 import BeautifulSoup
from test_ai_recommendations import FakeAIClient, _listing
from test_ai_recommendations import factory as factory
from test_ai_recommendations import override_db as override_db

from app.config import SearchContextConfig
from app.models import (
    ApartmentGroup,
    CollectorRun,
    DeletedContextSlug,
    Listing,
    ListingAIReview,
    ListingObservation,
    PriceHistory,
    Search,
    SearchContext,
    User,
)
from app.web import app
from services.ai_recommendations import (
    AIContextChanged,
    compact_listing_payload,
    input_hash,
    review_listing_with_cache,
)
from services.auth import SESSION_COOKIE_NAME, create_user_session, hash_password
from services.context_management import collector_guard, context_deletion_plan, delete_context_data
from services.search_contexts import upsert_context


async def seed(session):
    user = User(username="owner", display_name="Owner", password_hash=hash_password("secret"))
    session.add(user)
    await session.flush()
    context = SearchContext(slug="mine", name="Mine", owner_user_id=user.id, expected_rooms=3)
    other = SearchContext(slug="other", name="Other", owner_user_id=user.id, expected_rooms=3)
    session.add_all([context, other])
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
        for c in (context, other)
    ]
    exclusive = _listing(source_listing_id="exclusive", last_seen_at=datetime.now(UTC))
    shared = _listing(source_listing_id="shared", last_seen_at=datetime.now(UTC))
    session.add_all([*searches, exclusive, shared])
    await session.flush()
    session.add_all(
        [
            ListingObservation(search_id=searches[0].id, listing_id=exclusive.id),
            ListingObservation(search_id=searches[0].id, listing_id=shared.id),
            ListingObservation(search_id=searches[1].id, listing_id=shared.id),
            PriceHistory(listing_id=exclusive.id, new_price_rub=100),
            PriceHistory(listing_id=shared.id, new_price_rub=200),
        ]
    )
    await session.flush()
    return user, context, other, searches, exclusive, shared


@pytest.mark.parametrize("foreign_keys", [False, True])
async def test_delete_cleans_owned_data_preserves_shared_and_blocks_reimport(factory, foreign_keys):
    async with factory() as session:
        await session.execute(sa.text(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}"))
        user, context, other, searches, exclusive, shared = await seed(session)
        client = FakeAIClient()
        for ctx, listing in ((context, exclusive), (context, shared), (other, shared)):
            await review_listing_with_cache(
                session,
                listing=listing,
                context=ctx,
                user=user,
                model_name="test",
                prompt_version="v1",
                client=client,
            )
        await session.flush()
        plan = await context_deletion_plan(session, context)
        assert plan["listings"] == [exclusive.id]
        assert plan["shared"] == 1
        await delete_context_data(session, context)
        await session.commit()
        assert await session.scalar(sa.select(sa.func.count()).select_from(SearchContext)) == 1
        assert list(await session.scalars(sa.select(Listing.id))) == [shared.id]
        assert list(await session.scalars(sa.select(Search.id))) == [searches[1].id]
        assert list(await session.scalars(sa.select(PriceHistory.listing_id))) == [shared.id]
        assert list(await session.scalars(sa.select(ListingAIReview.context_id))) == [other.id]
        assert await session.get(DeletedContextSlug, "mine") is not None
        assert (
            await upsert_context(session, SearchContextConfig(slug="mine", name="Config")) is None
        )
        assert not (await session.execute(sa.text("PRAGMA foreign_key_check"))).all()


async def test_delete_keeps_shared_group_and_rejects_active_collection(factory):
    async with factory() as session:
        _, context, _, searches, exclusive, shared = await seed(session)
        group = ApartmentGroup()
        session.add(group)
        await session.flush()
        exclusive.group_id = shared.group_id = group.id
        await session.flush()
        assert (await context_deletion_plan(session, context))["listings"] == []
        run = CollectorRun(search_id=searches[0].id, status="started")
        session.add(run)
        await session.flush()
        with pytest.raises(ValueError, match="идёт сбор"):
            await delete_context_data(session, context)


async def test_edit_owned_context_invalidates_reviews_and_enforces_budget(factory, override_db):
    async with factory() as session:
        user, context, _, _, exclusive, _ = await seed(session)
        await review_listing_with_cache(
            session,
            listing=exclusive,
            context=context,
            user=user,
            model_name="test",
            prompt_version="v1",
            client=FakeAIClient(),
        )
        _, token = await create_user_session(session, user, days=1)
        context_id = context.id
        await session.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        response = await client.post(
            f"/contexts/{context_id}/edit",
            data={
                "name": "Updated",
                "price_max": "8000000",
                "ai_preferences": "Нужна остановка рядом",
            },
        )
        assert response.status_code == 303
        page = await client.get("/?context=mine&price_max=30000000")
        assert not BeautifulSoup(page.text, "html.parser").select("tr[data-ai-key]")
        page = await client.get(f"/contexts/{context_id}/edit")
        assert "Нужна остановка рядом" in page.text
    async with factory() as session:
        ctx = await session.get(SearchContext, context_id)
        assert ctx.price_max == 8_000_000
        assert ctx.ai_preferences == "Нужна остановка рядом"
        assert await session.scalar(sa.select(sa.func.count()).select_from(ListingAIReview)) == 0
        imported = await upsert_context(session, SearchContextConfig(slug="mine", name="Old name"))
        assert imported.name == "Updated" and imported.price_max == 8_000_000


async def test_context_permissions_confirmation_and_backup(factory, override_db, tmp_path):
    async with factory() as session:
        owner, context, _, _, _, _ = await seed(session)
        intruder = User(
            username="other", display_name="Other", password_hash=hash_password("secret")
        )
        session.add(intruder)
        await session.flush()
        _, foreign_token = await create_user_session(session, intruder, days=1)
        _, owner_token = await create_user_session(session, owner, days=1)
        context_id = context.id
        await session.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.cookies.set(SESSION_COOKIE_NAME, foreign_token)
        for suffix in ("edit", "delete"):
            assert (await client.get(f"/contexts/{context_id}/{suffix}")).status_code == 404
            assert (
                await client.post(
                    f"/contexts/{context_id}/{suffix}", data={"name": "Bad", "confirm_name": "Mine"}
                )
            ).status_code == 404
        client.cookies.set(SESSION_COOKIE_NAME, owner_token)
        assert (
            await client.post(f"/contexts/{context_id}/delete", data={"confirm_name": "wrong"})
        ).status_code == 422
        assert (
            await client.post(f"/contexts/{context_id}/delete", data={"confirm_name": "Mine"})
        ).status_code == 303
    backups = list((tmp_path / "backups").glob("*.gz"))
    assert len(backups) == 1
    with gzip.open(backups[0], "rb") as backup:
        assert backup.read(16) == b"SQLite format 3\x00"


async def test_ai_preferences_change_hash_and_discard_inflight_result(factory):
    async with factory() as session:
        user, context, _, _, listing, _ = await seed(session)
        context.ai_preferences = "Рядом остановка"
        payload = compact_listing_payload(listing, context)
        assert payload["context"]["ai_preferences"] == "Рядом остановка"
        before = input_hash(payload, model_name="test", prompt_version="v1")
        context.ai_preferences = "Тихий двор"
        assert before != input_hash(
            compact_listing_payload(listing, context), model_name="test", prompt_version="v1"
        )

        class ChangingClient(FakeAIClient):
            async def review_listing(self, payload):
                context.ai_preferences = "Другое пожелание"
                await session.flush()
                return await super().review_listing(payload)

        with pytest.raises(AIContextChanged):
            await review_listing_with_cache(
                session,
                listing=listing,
                context=context,
                user=user,
                model_name="test",
                prompt_version="v1",
                client=ChangingClient(),
            )
        assert await session.scalar(sa.select(sa.func.count()).select_from(ListingAIReview)) == 0


def test_context_migration_from_existing_sqlite(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'old.sqlite3'}")
    metadata = sa.MetaData()
    old = sa.Table(
        "search_contexts",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rules", sa.JSON()),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            old.insert().values(
                id=uuid.uuid4(),
                rules={"price_max": "30000000", "area_min": "50.5", "created_from_ui": True},
            )
        )
        with Operations.context(MigrationContext.configure(connection)):
            importlib.import_module("migrations.versions.0007_context_settings").upgrade()
        result = connection.execute(
            sa.text("SELECT price_max, area_min FROM search_contexts")
        ).one()
        assert tuple(result) == (30_000_000, 50.5)
    engine.dispose()


async def test_live_collector_lock_blocks_deletion(factory, tmp_path):
    fcntl = pytest.importorskip("fcntl")
    with (tmp_path / "collector.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        async with factory() as session:
            with pytest.raises(ValueError, match="идёт сбор"), collector_guard(session):
                pytest.fail("Acquired an occupied collector lock")


def test_capacity_migration_preserves_existing_active_contexts(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'capacity.sqlite3'}")
    metadata = sa.MetaData()
    users = sa.Table("users", metadata, sa.Column("id", sa.Uuid(), primary_key=True))
    contexts = sa.Table(
        "search_contexts",
        metadata,
        sa.Column("owner_user_id", sa.Uuid()),
        sa.Column("enabled", sa.Boolean()),
    )
    metadata.create_all(engine)
    existing, empty = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(users.insert(), [{"id": existing}, {"id": empty}])
        connection.execute(
            contexts.insert(),
            [
                {"owner_user_id": existing, "enabled": True},
                {"owner_user_id": existing, "enabled": True},
                {"owner_user_id": existing, "enabled": False},
            ],
        )
        with Operations.context(MigrationContext.configure(connection)):
            importlib.import_module("migrations.versions.0008_user_context_limit").upgrade()
        rows = connection.execute(sa.text("SELECT id, context_limit FROM users")).all()
        assert dict(rows) == {existing.hex: 2, empty.hex: 1}
    engine.dispose()
