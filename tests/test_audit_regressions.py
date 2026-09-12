from datetime import UTC, datetime, timedelta

import pytest

from app.models import Listing, Search, SearchContext
from app.runner import search_is_due
from services.geography import distance_km, in_context, usable_coordinates
from services.normalization import detect_district, mortgage_features, should_exclude_listing


@pytest.mark.parametrize("city", ["Тольятти", "Сызрань", "Новокуйбышевск", "Кинель"])
def test_other_city_not_accepted_as_samara(city):
    assert should_exclude_listing(
        title="3-комнатная квартира",
        description=None,
        property_type="flat",
        rooms=3,
        address_raw=f"Самарская область, {city}, Приморский бульвар, 42",
        address_normalized=None,
        floors_total=9,
        expected_rooms=3,
    )


def test_description_of_transport_does_not_change_city():
    assert not should_exclude_listing(
        title="3-комнатная квартира",
        description="Автобус в Тольятти и поселок",
        property_type="flat",
        rooms=3,
        address_raw="Самара, Ново-Садовая, 100",
        address_normalized=None,
        floors_total=9,
        expected_rooms=3,
    )


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("Самарская область, Тольятти, р-н Автозаводский", None),
        ("Самарская область, Самара, улица Самарская, 100", None),
        ("Самарская область, Самара, р-н Ленинский", "ленинский"),
        ("Самара, Самарский район, улица Куйбышева, 10", "самарский"),
        ("Самара, Кировский, Победы, 1", "кировский"),
    ],
)
def test_district_not_inferred_from_region_or_street(address, expected):
    assert detect_district(address) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ипотека не подходит", False),
        ("Не подходит под ипотеку", False),
        ("Ипотека невозможна", False),
        ("Только наличные", False),
        ("Возможна ипотека", True),
        ("Подходит под ипотеку", True),
        ("Ипотека", None),
        ("Подходит для семьи", None),
    ],
)
def test_mortgage_requires_positive_evidence(text, expected):
    assert mortgage_features(text)["mortgage_available"] is expected


def test_family_program_requires_mortgage_and_eligibility():
    assert mortgage_features("Квартира для семьи")["family_mortgage"] is None
    assert mortgage_features("Возможна семейная ипотека")["family_mortgage"] is True
    assert mortgage_features("Семейная ипотека не подходит")["family_mortgage"] is False


def test_radius_filters_unknown_and_outside_separately():
    context = SearchContext(
        object_type="land", radius_km=50, center_latitude=53.195873, center_longitude=50.100193
    )
    inside = Listing(latitude=53.3, longitude=50.2)
    outside = Listing(latitude=54.2, longitude=50.1)
    missing = Listing()
    fallback = Listing(latitude=53.195538, longitude=50.101783)
    assert in_context(inside, context)
    assert not in_context(outside, context)
    assert not in_context(missing, context)
    assert not in_context(fallback, context)
    assert in_context(missing, context, "unknown")
    assert in_context(fallback, context, "unknown")
    assert not in_context(outside, context, "unknown")
    assert not usable_coordinates(float("nan"), 50)
    assert distance_km(53, 50, 53, 50) == 0


def test_configured_search_interval_is_respected():
    now = datetime.now(UTC)
    search = Search(
        interval_hours=12,
        last_status="completed",
        last_completed_at=(now - timedelta(hours=3)).replace(tzinfo=None),
    )
    assert not search_is_due(search, now)
    assert search_is_due(search, now + timedelta(hours=9))
    search.last_status = "failed"
    assert search_is_due(search, now)


async def test_browser_startup_error_is_visible_in_run_history(tmp_path, monkeypatch):
    from sqlalchemy import select

    import app.runner as runner
    from app.config import Settings
    from app.db import create_engine, create_session_factory
    from app.models import Base, CollectorRun

    config = tmp_path / "searches.yaml"
    config.write_text("searches: []", encoding="utf-8")
    scoring = tmp_path / "scoring.yaml"
    scoring.write_text("{}", encoding="utf-8")
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'runs.sqlite3'}",
        searches_config_path=config,
        scoring_config_path=scoring,
    )
    engine = create_engine(settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def failed_browser(_settings):
        raise ConnectionError("CDP unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(runner, "persistent_context", failed_browser)
    with pytest.raises(ConnectionError):
        await runner.collect_once(settings)
    async with create_session_factory(engine)() as session:
        run = (await session.scalars(select(CollectorRun))).one()
        assert run.status == "failed"
        assert run.finished_at is not None
        assert "CDP unavailable" in run.error_message
    await engine.dispose()
