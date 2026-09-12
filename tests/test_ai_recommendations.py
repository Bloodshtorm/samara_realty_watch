from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import (
    Base,
    Listing,
    ListingObservation,
    ListingUserState,
    Search,
    SearchContext,
    User,
)
from app.web import app, db_session
from services.ai_recommendations import (
    compact_listing_payload,
    input_hash,
    parse_ai_response,
    review_listing_with_cache,
)
from services.auth import SESSION_COOKIE_NAME, create_user_session, hash_password


class FakeAIClient:
    def __init__(self) -> None:
        self.calls = 0
        self.payloads: list[dict] = []

    async def review_listing(self, payload: dict) -> dict:
        self.calls += 1
        self.payloads.append(payload)
        return {
            "ai_score": 81,
            "verdict": "watch",
            "pros": ["Цена ниже похожих вариантов"],
            "cons": ["Описание короткое"],
            "risks": ["Проверить документы"],
            "summary": "Стоит посмотреть после проверки адреса.",
        }


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ai.sqlite3'}")
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


def _listing(**kwargs) -> Listing:
    values = {
        "source": "cian",
        "source_listing_id": "42",
        "url": "https://example.test/42",
        "canonical_url": "https://example.test/42",
        "title": "3-комнатная квартира",
        "description": "Светлая квартира без телефона в payload.",
        "rooms": 3,
        "area_total_m2": 74.5,
        "price_rub": 8_900_000,
        "price_per_m2": 119_463,
        "floor": 4,
        "floors_total": 9,
        "district": "Октябрьский",
        "score": 72,
        "score_reasons": ["ниже сегмента"],
        "phone_masked": "+7 ***",
        "raw_payload": {"secret": "do-not-send"},
        "features": {"mortgage_available": True, "irrelevant": "skip"},
        "is_active": True,
        "last_seen_at": datetime.now(UTC),
    }
    values.update(kwargs)
    return Listing(**values)


def test_compact_payload_and_hash_are_stable() -> None:
    context = SearchContext(slug="ctx", name="Context", expected_rooms=3, rules={"price_max": 9})
    listing = _listing()

    payload = compact_listing_payload(listing, context)
    assert "raw_payload" not in str(payload)
    assert "phone" not in str(payload).lower()
    assert payload["listing"]["features"] == {"mortgage_available": True}

    first = input_hash(payload, model_name="qwen3:14b", prompt_version="v1")
    second = input_hash(payload, model_name="qwen3:14b", prompt_version="v1")
    changed = input_hash(payload, model_name="qwen3:14b", prompt_version="v2")
    assert first == second
    assert first != changed


def test_parse_ai_response_extracts_json_and_clamps() -> None:
    parsed = parse_ai_response(
        '<think>draft</think>{"ai_score": 120, "verdict": "watch", '
        '"pros": ["a"], "cons": ["b"], "risks": ["c"], "summary": "ok"}'
    )
    assert parsed["ai_score"] == 100
    assert parsed["verdict"] == "watch"
    assert parsed["pros"] == ["a"]


async def test_review_listing_reuses_cached_result(factory) -> None:
    client = FakeAIClient()
    async with factory() as session, session.begin():
        user = User(username="u", display_name="U", password_hash=hash_password("secret"))
        context = SearchContext(slug="ctx", name="Context", owner_user_id=user.id)
        listing = _listing()
        session.add_all([user, context, listing])
        await session.flush()

        first, created = await review_listing_with_cache(
            session,
            listing=listing,
            context=context,
            user=user,
            model_name="qwen3:14b",
            prompt_version="v1",
            client=client,
        )
        second, reused_created = await review_listing_with_cache(
            session,
            listing=listing,
            context=context,
            user=user,
            model_name="qwen3:14b",
            prompt_version="v1",
            client=client,
        )

    assert created is True
    assert reused_created is False
    assert first.id == second.id
    assert client.calls == 1


async def test_ai_endpoint_uses_current_filters_and_skips_hidden(
    factory, override_db, monkeypatch
) -> None:
    captured: dict[str, object] = {"calls": []}

    async def fake_review_listings(*args, **kwargs):
        listings = kwargs["listings"]
        captured["calls"].append([item.source_listing_id for item in listings])
        captured["model"] = kwargs["model_name"]
        return {"created": len(listings), "reused": 0, "total": len(listings)}

    async def fake_review_listing_with_cache(*args, **kwargs):
        listing = kwargs["listing"]
        captured["calls"].append([listing.source_listing_id])
        return (
            SimpleNamespace(
                listing_id=listing.id,
                model_name=kwargs["model_name"],
                ai_score=77,
                verdict="watch",
                summary="Стоит посмотреть.",
                pros=[],
                cons=[],
                risks=[],
                updated_at=datetime.now(UTC),
            ),
            True,
        )

    monkeypatch.setenv("AI_RECOMMENDATIONS_ENABLED", "true")
    monkeypatch.setenv("AI_RECOMMENDATION_LIMIT", "10")
    monkeypatch.setattr("app.web.review_listings", fake_review_listings)
    monkeypatch.setattr("app.web.review_listing_with_cache", fake_review_listing_with_cache)
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        user = User(username="u", display_name="U", password_hash=hash_password("secret"))
        session.add(user)
        await session.flush()
        context = SearchContext(slug="ctx", name="Context", owner_user_id=user.id, expected_rooms=3)
        session.add(context)
        await session.flush()
        search = Search(
            context_id=context.id,
            name="search",
            source="cian",
            url="https://example.test",
            city="Самара",
            rooms=3,
        )
        visible = _listing(source_listing_id="visible", price_rub=8_000_000, last_seen_at=now)
        other_visible = _listing(source_listing_id="other", price_rub=8_500_000, last_seen_at=now)
        too_expensive = _listing(
            source_listing_id="expensive", price_rub=12_000_000, last_seen_at=now
        )
        hidden = _listing(source_listing_id="hidden", price_rub=7_000_000, last_seen_at=now)
        session.add_all([search, visible, other_visible, too_expensive, hidden])
        await session.flush()
        for listing in (visible, other_visible, too_expensive, hidden):
            session.add(ListingObservation(listing_id=listing.id, search_id=search.id))
        session.add(ListingUserState(user_id=user.id, listing_id=hidden.id, is_hidden=True))
        visible_id = str(visible.id)
        _, token = await create_user_session(session, user, days=1)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.cookies.set(SESSION_COOKIE_NAME, token)
        response = await client.post("/ai/recommendations/run?context=ctx&price_max=9000000")
        selected_response = await client.post(
            "/api/ai/recommendations/review?context=ctx&price_max=9000000",
            json={"selected_listing_id": visible_id},
        )
        hidden_response = await client.post(
            "/ai/recommendations/run?context=ctx&price_max=9000000&view=hidden"
        )
        hidden_selected_response = await client.post(
            "/api/ai/recommendations/review?context=ctx&price_max=9000000&view=hidden",
            json={"selected_listing_id": visible_id},
        )

    assert response.status_code == 303
    assert selected_response.status_code == 200
    row_html = selected_response.json()["rows_html"]
    assert f'data-ai-key="{visible_id}"' in row_html
    assert "Стоит посмотреть." in row_html
    assert "Полный AI-разбор" in row_html
    assert 'data-ai-title="other"' not in row_html
    assert hidden_response.status_code == 303
    assert hidden_selected_response.status_code == 404
    assert captured["calls"] == [["visible", "other"], ["visible"], []]
    assert captured["model"] == Settings().ollama_model
