import importlib
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select
from test_ai_recommendations import factory as factory
from test_ai_recommendations import override_db as override_db

from app.config import Settings
from app.models import CollectorRun, ListingObservation, Search, SearchContext, User
from app.schemas import ParsedListing
from app.web import app
from services.auth import SESSION_COOKIE_NAME, create_user_session, hash_password


def test_request_migration_enrolls_only_active_ui_contexts(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'old.sqlite3'}")
    metadata = sa.MetaData()
    contexts = sa.Table(
        "search_contexts",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rules", sa.JSON()),
        sa.Column("enabled", sa.Boolean()),
    )
    searches = sa.Table(
        "searches", metadata, sa.Column("context_id", sa.Uuid()), sa.Column("enabled", sa.Boolean())
    )
    ids = [uuid.uuid4() for _ in range(3)]
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(
            contexts.insert(),
            [
                {"id": ids[0], "rules": {"created_from_ui": True}, "enabled": True},
                {"id": ids[1], "rules": {}, "enabled": True},
                {"id": ids[2], "rules": {"created_from_ui": True}, "enabled": False},
            ],
        )
        connection.execute(
            searches.insert(), [{"context_id": uid, "enabled": False} for uid in ids]
        )
        migration = importlib.import_module("migrations.versions.0010_collection_requests")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        rows = connection.execute(
            sa.text("SELECT auto_collect, collection_requested_at, enabled FROM searches")
        ).all()
        assert rows[0][0] == 1 and rows[0][1] and not rows[0][2]
        assert rows[1:] == [(0, None, 0), (0, None, 0)]
    engine.dispose()


async def test_creation_starts_collection_and_subscription_blocks_extra_context(
    factory, override_db
):
    async with factory() as session:
        user = User(
            username="subscriber", display_name="Subscriber", password_hash=hash_password("secret")
        )
        session.add(user)
        await session.flush()
        _, token = await create_user_session(session, user, days=1)
        await session.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        data = {
            "name": "My flat",
            "object_type": "flat",
            "expected_rooms": "3",
            "sources": ["n1", "etagi"],
        }
        response = await client.post("/contexts", data=data)
        assert response.status_code == 303
        page = await client.get(response.headers["location"])
        assert "Поиск запускается или выполняется" in page.text
        assert (await client.post("/contexts", data=data)).status_code == 409
    async with factory() as session:
        contexts = list(await session.scalars(select(SearchContext)))
        assert len(contexts) == 1
        searches = list(await session.scalars(select(Search)))
        assert len(searches) == 2
        assert all(s.auto_collect and s.collection_requested_at and not s.enabled for s in searches)


def test_automatic_retry_respects_interval():
    from app.runner import search_is_due

    now = datetime.now(UTC)
    search = Search(auto_collect=True, interval_hours=12, last_status="failed", last_started_at=now)
    assert not search_is_due(search, now + timedelta(hours=1))
    assert search_is_due(search, now + timedelta(hours=12))


async def seed_requests(factory):
    async with factory() as session:
        admin = User(
            username="admin",
            display_name="Admin",
            role="admin",
            password_hash=hash_password("secret"),
        )
        owner = User(username="owner", display_name="Owner", password_hash=hash_password("secret"))
        session.add_all([admin, owner])
        await session.flush()
        context = SearchContext(slug="ui", name="UI", owner_user_id=owner.id, expected_rooms=3)
        other = SearchContext(slug="private", name="Private", owner_user_id=admin.id)
        session.add_all([context, other])
        await session.flush()
        searches = [
            Search(
                context_id=c.id,
                name=c.slug,
                source="n1",
                city="Самара",
                rooms=3,
                url="https://example.test/search",
                enabled=False,
                max_pages=20,
            )
            for c in (context, other)
        ]
        session.add_all(searches)
        _, admin_token = await create_user_session(session, admin, days=1)
        _, owner_token = await create_user_session(session, owner, days=1)
        await session.commit()
        return context.id, other.id, searches[0].id, searches[1].id, admin_token, owner_token


async def test_admin_can_queue_disabled_search_without_enabling_and_double_click(
    factory, override_db
):
    ctx, other, search_id, foreign_id, admin_token, owner_token = await seed_requests(factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.cookies.set(SESSION_COOKIE_NAME, owner_token)
        assert (await client.post(f"/contexts/{ctx}/collection")).status_code == 403
        assert (await client.get(f"/contexts/{other}/collection")).status_code == 404
        page = await client.get(f"/contexts/{ctx}/collection")
        assert page.status_code == 200 and 'type="submit"' not in page.text
        client.cookies.set(SESSION_COOKIE_NAME, admin_token)
        assert (
            await client.post(f"/contexts/{ctx}/collection", data={"search_id": str(foreign_id)})
        ).status_code == 404
        assert (await client.post(f"/contexts/{ctx}/collection")).status_code == 303
        async with factory() as session:
            requested_at = (await session.get(Search, search_id)).collection_requested_at
        assert (await client.post(f"/contexts/{ctx}/collection")).status_code == 303
        page = await client.get(f"/contexts/{ctx}/collection?fragment=true")
        assert 'data-pending="true"' in page.text and "В очереди" in page.text
    async with factory() as session:
        search = await session.get(Search, search_id)
        assert search.collection_requested_at == requested_at
        assert not search.enabled
        assert (await session.get(Search, foreign_id)).collection_requested_at is None


@pytest.mark.parametrize("outcome", ["success", "failed", "browser_failed", "paused"])
@pytest.mark.parametrize("automatic", [False, True])
async def test_worker_consumes_db_only_requests_once(
    factory, tmp_path, monkeypatch, outcome, automatic
):
    import app.runner as runner
    from collectors.avito import AvitoCollector
    from services.avito_policy import AvitoPolicy

    ctx, _, search_id, foreign_id, _, _ = await seed_requests(factory)
    async with factory() as session:
        search = await session.get(Search, search_id)
        search.collection_requested_at = datetime.now(UTC)
        search.auto_collect = automatic
        if outcome == "paused":
            search.source = "avito"
        await session.commit()
    config = tmp_path / "searches.yaml"
    config.write_text("contexts: []\nsearches: []\n", encoding="utf-8")
    scoring = tmp_path / "scoring.yaml"
    scoring.write_text("{}", encoding="utf-8")
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'ai.sqlite3'}",
        searches_config_path=config,
        scoring_config_path=scoring,
        avito_policy_path=tmp_path / "policy.sqlite3",
        telegram_bot_token=None,
        telegram_chat_id=None,
    )
    calls = []

    async def browser(_settings):
        if outcome == "browser_failed":
            raise ConnectionError("CDP unavailable")
        yield None

    class Collector:
        pages_processed = 1

        async def collect_search(self, search, context):
            calls.append((search.id, search.max_pages))
            if outcome == "failed":
                raise RuntimeError("source unavailable")
            return [
                ParsedListing(
                    source="n1",
                    source_listing_id="one",
                    url="https://example.test/1",
                    canonical_url="https://example.test/1",
                    title="3-комнатная квартира",
                    rooms=3,
                    floors_total=9,
                    area_total_m2=80,
                    price_rub=9000000,
                )
            ]

    monkeypatch.setattr(runner, "persistent_context", browser)
    monkeypatch.setattr(runner, "COLLECTORS", {"n1": Collector(), "avito": AvitoCollector()})
    if outcome == "paused":
        AvitoPolicy(settings.avito_policy_path).block("Manual verification required")
    if outcome == "browser_failed":
        with pytest.raises(ConnectionError):
            await runner.collect_once(settings, requested_only=True)
    else:
        await runner.collect_once(settings, requested_only=True)
    async with factory() as session:
        search = await session.get(Search, search_id)
        assert search.collection_requested_at is None
        assert search.max_pages == 20
        assert search.enabled == (automatic and outcome == "success")
        assert (await session.get(Search, foreign_id)).last_started_at is None
        if outcome == "success":
            assert search.last_status == "completed"
            assert list(await session.scalars(select(ListingObservation.search_id))) == [search_id]
        else:
            assert search.last_status == ("paused" if outcome == "paused" else "failed")
            assert search.last_error
        runs_before = len(list(await session.scalars(select(CollectorRun))))
    await runner.collect_once(settings, requested_only=True)
    async with factory() as session:
        assert len(list(await session.scalars(select(CollectorRun)))) == runs_before
    assert calls == ([] if outcome in {"paused", "browser_failed"} else [(search_id, 1)])
    if automatic and outcome == "success":
        await runner.collect_once(settings, due_only=True)
        assert calls == [(search_id, 1)]
        async with factory() as session:
            search = await session.get(Search, search_id)
            search.last_started_at = datetime.now(UTC) - timedelta(hours=24)
            await session.commit()
        await runner.collect_once(settings, due_only=True)
        assert calls == [(search_id, 1), (search_id, 20)]
