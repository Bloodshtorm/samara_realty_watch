from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    ApartmentGroup,
    Base,
    Listing,
    ListingLink,
    ListingObservation,
    ListingUserState,
    Search,
    SearchContext,
)
from app.web import (
    ListingFilters,
    _apartment_summaries,
    _filtered_listings_query,
    _map_points,
    app,
    db_session,
    unique_apartments,
)
from services.apartments import candidate_pairs, confirm_link, reconcile_groups, split_member
from services.deduplication import building_key, compare_listings

DESCRIPTION = (
    "Номер объекта: 123456. Просторная квартира с индивидуальной планировкой. "
    "Комнаты площадью 16,9 и 13,7 кв.м, кухня гостиная 25,9 кв.м. "
    "В прихожей расположен встроенный шкаф, мебель и техника остаются покупателю. "
    "Окна спальни выходят на тихий двор, лоджия утеплена, на полу паркет. "
    "Заменены трубы, установлены счетчики, предусмотрена гардеробная и рабочий кабинет. "
    "Дом кирпичный, территория закрытая, два лифта, рядом школа, парк и остановка."
)


def listing(**kwargs):
    values = dict(
        id=uuid4(),
        source="domclick",
        source_listing_id=str(uuid4()),
        url="https://example.test/1",
        canonical_url="https://example.test/1",
        property_type="flat",
        address_normalized="самара, улица тестовая, 140",
        rooms=3,
        floor=18,
        floors_total=19,
        area_total_m2=77,
        price_rub=14_280_000,
        price_per_m2=185454,
        latitude=53.213163,
        longitude=50.211758,
        title="Квартира",
        description=DESCRIPTION,
        is_active=True,
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )
    values.update(kwargs)
    return Listing(**values)


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.sqlite3'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def test_three_source_regression_and_photo_provenance():
    a = listing()
    b = listing(
        source="n1",
        property_type="flats",
        area_total_m2=77.8,
        address_normalized="самара, советский район, тестовая, 140",
        raw_payload={"photos": [{"original": "/cian/101.jpg"}, {"original": "/cian/102.jpg"}]},
    )
    c = listing(
        source="cian",
        property_type="flatSale",
        area_total_m2=77.8,
        address_normalized=(
            "самарская область, самара, р-н советский, м. победа, улица тестовая, 140"
        ),
        raw_payload={"photos": [{"id": 101}, {"id": 102}]},
    )
    for x, y in [(a, b), (a, c), (b, c)]:
        result = compare_listings(x, y)
        assert result and result.automatic
    b.description = c.description = None
    assert compare_listings(b, c).automatic
    b.source = "avito"
    assert not compare_listings(b, c).automatic


@pytest.mark.parametrize(
    "change",
    [
        {"floor": 2},
        {"rooms": 2},
        {"floors_total": 18},
        {"floor": None},
        {"address_normalized": "самара, улица тестовая, 140 корпус 2"},
        {"area_total_m2": 90},
        {"property_type": "land"},
        {"description": "Прекрасная квартира выгодное предложение. " * 20},
    ],
)
def test_negative_pairs(change):
    result = compare_listings(listing(), listing(**change))
    assert result is None or not result.automatic


def test_address_only_and_generic_template_are_not_automatic():
    a = listing(rooms=None, floor=None, area_total_m2=None, description=None)
    assert compare_listings(a, listing()) is None
    template = "Прекрасная квартира выгодное предложение. " * 20
    result = compare_listings(listing(description=template), listing(description=template))
    assert result and not result.automatic
    assert building_key(
        listing(address_normalized="самара, улица тестовая, 140к2")
    ) != building_key(listing())
    assert not candidate_pairs(
        [
            listing(latitude=None, longitude=None),
            listing(address_normalized="самара, другая улица, 9", latitude=None, longitude=None),
        ]
    )


async def test_grouping_flags_split_rejection_and_recollection(factory):
    async with factory() as session, session.begin():
        a, b, c = (
            listing(),
            listing(source="n1", area_total_m2=77.8),
            listing(source="cian", area_total_m2=77.8),
        )
        session.add_all([a, b, c])
        await session.flush()
        session.add_all(
            [
                ListingUserState(listing_id=a.id, is_favorite=True, is_hidden=True),
                ListingUserState(listing_id=b.id, is_hidden=True),
            ]
        )
        await session.flush()
        await reconcile_groups(session)
        assert a.group_id == b.group_id == c.group_id
        group = await session.get(ApartmentGroup, a.group_id)
        assert group.is_favorite and not group.is_hidden
        assert len((await session.scalars(select(Listing))).all()) == 3
        summary = (await _apartment_summaries(session, [a]))[a.id]
        assert summary["source_count"] == 3
        assert "77" in summary["area"] and "77,8" in summary["area"]
        assert len(unique_apartments([a, b, c])) == 1
        assert len(_map_points(unique_apartments([a, b, c]), {}, {a.id: summary})) == 1
        a.price_rub = 18_000_000
        await reconcile_groups(session)
        assert a.group_id == b.group_id == c.group_id
        a.floor = 17
        await reconcile_groups(session)
        assert group.needs_review
        original = group.id
        separated = await split_member(session, original, a.id)
        a.floor = 18
        a.price_rub = 14_280_000
        await reconcile_groups(session)
        assert a.group_id == separated and b.group_id == c.group_id == original
        new = await session.get(ApartmentGroup, separated)
        assert new.is_favorite == group.is_favorite
        rejected = (
            await session.scalars(select(ListingLink).where(ListingLink.status == "rejected"))
        ).first()
        await confirm_link(session, rejected)
        assert a.group_id == b.group_id == c.group_id


async def test_complete_link_not_transitive(factory):
    async with factory() as session, session.begin():
        items = [listing(area_total_m2=area) for area in [77, 78, 79.1]]
        session.add_all(items)
        await session.flush()
        await reconcile_groups(session)
        assert len({item.group_id for item in items}) == 2
        await reconcile_groups(session)
        assert len({item.group_id for item in items}) == 2


async def test_filters_routes_and_group_actions(factory):
    async with factory() as session, session.begin():
        context = SearchContext(slug="3rooms_samara", name="Flats", expected_rooms=3)
        session.add(context)
        await session.flush()
        search = Search(
            context_id=context.id,
            name="test",
            source="domclick",
            url="https://example.test",
            city="Samara",
        )
        session.add(search)
        await session.flush()
        a, b = (
            listing(price_rub=100, area_total_m2=77),
            listing(source="cian", price_rub=104, area_total_m2=78),
        )
        session.add_all([a, b])
        await session.flush()
        for item in [a, b]:
            session.add(
                ListingObservation(
                    listing_id=item.id, search_id=search.id, price_rub=item.price_rub
                )
            )
        await session.flush()
        await reconcile_groups(session)
        stmt = _filtered_listings_query(ListingFilters(price_max=101, area_min=77.5), context)
        assert not (await session.scalars(stmt)).all()
        stmt = _filtered_listings_query(ListingFilters(source="cian"), context)
        assert [item.id for item in (await session.scalars(stmt)).all()] == [b.id]
        group_id = a.group_id

    async def override():
        async with factory() as session:
            yield session

    app.dependency_overrides[db_session] = override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for route in ["/", "/duplicates", f"/apartments/{group_id}", f"/listings/{a.id}"]:
                response = await client.get(route)
                assert response.status_code == 200, response.text
            response = await client.post(
                "/api/listings/spatial",
                json={"mode": "bounds", "north": 54, "south": 53, "east": 51, "west": 50},
            )
            assert response.json()["total"] == 1
            assert response.json()["listing_ids"] == [str(group_id)]
            assert (await client.post(f"/listings/{b.id}/state?action=favorite")).status_code == 303
            assert "Найдено: 1" in (await client.get("/?view=favorites")).text
            await client.post(f"/listings/{a.id}/state?action=hide")
            assert "Найдено: 0" in (await client.get("/")).text
            assert "Найдено: 1" in (await client.get("/?view=hidden")).text
            assert (await client.get(f"/listings/{b.id}")).status_code == 200
    finally:
        app.dependency_overrides.clear()
