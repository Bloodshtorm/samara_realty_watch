import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Listing, PriceHistory, Search
from app.schemas import ParsedListing
from services.ingestion import upsert_listing


@pytest.fixture()
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_idempotent_listing_save(session_factory) -> None:
    async with session_factory() as session:
        async with session.begin():
            search = Search(
                name="test",
                source="yandex_realty",
                url="https://example.test",
                city="Самара",
                rooms=3,
            )
            session.add(search)
            await session.flush()
            parsed = ParsedListing(
                source="yandex_realty",
                source_listing_id="123",
                url="https://example.test/123",
                canonical_url="https://example.test/123",
                price_rub=8_900_000,
                area_total_m2=73.2,
            )
            first = await upsert_listing(session, search, parsed)
            second = await upsert_listing(session, search, parsed)
            assert first.created is True
            assert second.created is False
        async with session.begin():
            listings = (await session.execute(select(Listing))).scalars().all()
            history = (await session.execute(select(PriceHistory))).scalars().all()
            assert len(listings) == 1
            assert len(history) == 0


async def test_price_history_only_on_change(session_factory) -> None:
    async with session_factory() as session:
        async with session.begin():
            search = Search(
                name="test",
                source="yandex_realty",
                url="https://example.test",
                city="Самара",
                rooms=3,
            )
            session.add(search)
            await session.flush()
            parsed = ParsedListing(
                source="yandex_realty",
                source_listing_id="123",
                url="https://example.test/123",
                canonical_url="https://example.test/123",
                price_rub=8_900_000,
                area_total_m2=73.2,
            )
            await upsert_listing(session, search, parsed)
            changed = parsed.model_copy(update={"price_rub": 8_500_000})
            await upsert_listing(session, search, changed)
        async with session.begin():
            history = (await session.execute(select(PriceHistory))).scalars().all()
            assert len(history) == 1
            assert history[0].old_price_rub == 8_900_000
            assert history[0].new_price_rub == 8_500_000
