import importlib

import httpx
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select
from test_ai_recommendations import FakeAIClient, _listing
from test_ai_recommendations import factory as factory
from test_ai_recommendations import override_db as override_db
from test_context_management import seed

from app.models import AIReviewJob, ApartmentGroup, ListingAIReview, ListingUserState
from app.web import app
from services.ai_jobs import run_ai_job
from services.ai_recommendations import latest_reviews_for_listings, review_listing_with_cache
from services.auth import SESSION_COOKIE_NAME, create_user_session
from services.location_evidence import nearby_stops


def test_apartment_migration_preserves_rows_and_queues_only_initial_discovery(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.sqlite3'}")
    migration = importlib.import_module("migrations.versions.0011_apartment_experience")
    with engine.begin() as connection:
        for name in ("users", "search_contexts", "listings"):
            connection.exec_driver_sql(f"CREATE TABLE {name} (id CHAR(32) PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE searches (id INTEGER PRIMARY KEY, auto_collect BOOLEAN, enabled BOOLEAN)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE collector_runs (search_id INTEGER, pages_processed INTEGER)"
        )
        connection.exec_driver_sql("INSERT INTO searches VALUES (1,1,1),(2,1,1),(3,0,1)")
        connection.exec_driver_sql("INSERT INTO collector_runs VALUES (1,1),(2,20),(3,1)")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert connection.exec_driver_sql(
            "SELECT id, discovery_pending FROM searches ORDER BY id"
        ).all() == [(1, 1), (2, 0), (3, 0)]
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert connection.exec_driver_sql("SELECT count(*) FROM searches").scalar() == 3
    engine.dispose()


async def test_group_review_combines_visible_sources_and_force_reuses_row(factory):
    async with factory() as session:
        user, context, _, _, first, second = await seed(session)
        group = ApartmentGroup()
        session.add(group)
        await session.flush()
        first.group_id = second.group_id = group.id
        private = _listing(
            source_listing_id="private", description="PRIVATE_SENTINEL", group_id=group.id
        )
        hidden = _listing(
            source_listing_id="hidden", description="HIDDEN_SENTINEL", group_id=group.id
        )
        session.add_all([private, hidden])
        await session.flush()
        session.add(ListingUserState(user_id=user.id, listing_id=hidden.id, is_hidden=True))
        await session.flush()

        class Client(FakeAIClient):
            async def review_listing(self, payload):
                assert len(payload["sources"]) == 2
                assert "PRIVATE_SENTINEL" not in str(payload)
                assert "HIDDEN_SENTINEL" not in str(payload)
                return await super().review_listing(payload)

        client = Client()
        kwargs = dict(
            session=session,
            context=context,
            user=user,
            model_name="test",
            prompt_version="v2",
            client=client,
        )
        review, _ = await review_listing_with_cache(listing=first, **kwargs)
        cached, created = await review_listing_with_cache(listing=second, **kwargs)
        assert cached.id == review.id and not created and client.calls == 1
        refreshed, _ = await review_listing_with_cache(listing=second, force=True, **kwargs)
        assert refreshed.id == review.id and client.calls == 2
        assert len(list(await session.scalars(select(ListingAIReview)))) == 1
        reviews = await latest_reviews_for_listings(
            session, [first.id, second.id], user=user, context=context
        )
        assert reviews[first.id].id == reviews[second.id].id
        second.price_rub += 100000
        await session.flush()
        _, changed = await review_listing_with_cache(listing=first, **kwargs)
        assert changed


async def test_ai_job_queue_is_owner_scoped_and_double_click_safe(
    factory, override_db, monkeypatch
):
    import app.web as web

    monkeypatch.setenv("AI_RECOMMENDATIONS_ENABLED", "true")
    calls = []

    async def worker(job_id, database_url):
        calls.append(job_id)

    monkeypatch.setattr(web, "run_ai_job", worker)
    async with factory() as session:
        user, context, _, _, listing, _ = await seed(session)
        _, token = await create_user_session(session, user, days=1)
        listing_id = listing.id
        await session.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        url = f"/api/listings/{listing_id}/ai-jobs?context=mine"
        response = await client.post(url)
        assert response.status_code == 202
        assert (await client.post(url)).json() == response.json()
        assert len(calls) == 1
        status = await client.get("/api/ai-jobs/" + response.json()["job_id"])
        assert status.json()["status"] == "queued"
        detail = await client.get(f"/listings/{listing_id}?context=mine")
        assert "ai-start" in detail.text and "jobId" in detail.text
        client.cookies.clear()
        assert (await client.get("/api/ai-jobs/" + response.json()["job_id"])).status_code == 303


async def test_nearby_stops_cache_and_coordinates(factory, monkeypatch):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, data):
            calls.append(data)
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "elements": [
                        {
                            "type": "node",
                            "id": 1,
                            "lat": 53.2001,
                            "lon": 50.1,
                            "tags": {"name": "Тестовая остановка"},
                        }
                    ]
                },
            )

    monkeypatch.setattr("services.location_evidence.httpx.AsyncClient", Client)
    async with factory() as session:
        listing = _listing(latitude=53.2, longitude=50.1)
        first = await nearby_stops(session, listing)
        second = await nearby_stops(session, listing)
        assert first == second and len(calls) == 1
        assert first["stops"][0]["distance_m_straight_line"] < 20
        listing.latitude = None
        assert (await nearby_stops(session, listing))["status"] == "unknown"
        assert len(calls) == 1


async def test_background_worker_saves_result(factory, tmp_path, monkeypatch):
    class Client(FakeAIClient):
        def __init__(self, *args, **kwargs):
            super().__init__()

    monkeypatch.setattr("services.ai_jobs.OllamaClient", Client)
    async with factory() as session:
        user, context, _, _, listing, _ = await seed(session)
        job = AIReviewJob(user_id=user.id, context_id=context.id, listing_id=listing.id)
        session.add(job)
        await session.commit()
        job_id = job.id
    await run_ai_job(job_id, f"sqlite+aiosqlite:///{tmp_path / 'ai.sqlite3'}")
    async with factory() as session:
        assert (await session.get(AIReviewJob, job_id)).status == "completed"
        assert len(list(await session.scalars(select(ListingAIReview)))) == 1
