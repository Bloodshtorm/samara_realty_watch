from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Base,
    CollectorRun,
    Listing,
    ListingObservation,
    ListingUserState,
    PriceHistory,
    Search,
)
from scripts.compact_database import backup_database, compact_database
from services.retention import prune_history


@pytest.mark.parametrize("compact", [False, True])
async def test_retention_preserves_boundaries_membership_and_current_data(tmp_path, compact):
    path = tmp_path / "test.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        listing = Listing(
            source="avito",
            source_listing_id="1",
            url="https://example.test/1",
            canonical_url="https://example.test/1",
            description="Current",
            raw_payload={"keep": True},
        )
        searches = [
            Search(name=str(i), source="avito", url="https://example.test", city="Samara")
            for i in range(2)
        ]
        session.add_all([listing, *searches])
        await session.flush()
        session.add(ListingUserState(listing_id=listing.id, is_favorite=True))
        session.add(PriceHistory(listing_id=listing.id, old_price_rub=100, new_price_rub=90))
        # Second search is no longer collected, but its membership must survive pruning.
        for search, days in zip(searches, [[60, 50, 40, 10, 2, 1], [60, 50, 40]], strict=True):
            for day in days:
                session.add(
                    ListingObservation(
                        listing_id=listing.id,
                        search_id=search.id,
                        observed_at=now - timedelta(days=day),
                        price_rub=100 + day,
                        raw_payload={"large": "x" * 10_000},
                        description_snapshot="Old description",
                    )
                )
        # Tied timestamps still keep a deterministic pair of boundary rows.
        session.add(
            ListingObservation(
                listing_id=listing.id,
                search_id=searches[1].id,
                observed_at=now - timedelta(days=50),
                price_rub=150,
            )
        )
        for status, day in [("completed", 40), ("failed", 40), ("started", 40), ("completed", 1)]:
            session.add(
                CollectorRun(
                    status=status,
                    started_at=now - timedelta(days=day),
                    finished_at=None if status == "started" else now - timedelta(days=day),
                )
            )
    async with factory() as session, session.begin():
        await prune_history(session, now=now, compact=compact)
        await prune_history(session, now=now, compact=compact)
    async with factory() as session:
        observations = (await session.scalars(select(ListingObservation))).all()
        assert len(observations) == (4 if compact else 6)
        for search, expected in zip(
            searches, [[101, 160] if compact else [101, 102, 110, 160], [140, 160]], strict=True
        ):
            assert sorted(o.price_rub for o in observations if o.search_id == search.id) == expected
        assert all(o.raw_payload is None for o in observations)
        assert sum(o.description_snapshot is not None for o in observations) == (
            0 if compact else 2
        )
        assert (await session.get(Listing, listing.id)).raw_payload == {"keep": True}
        assert len((await session.scalars(select(ListingUserState))).all()) == 1
        assert len((await session.scalars(select(PriceHistory))).all()) == 1
        assert len((await session.scalars(select(CollectorRun))).all()) == 2
    await engine.dispose()
    if compact:
        backup = tmp_path / "backup.gz"
        backup_database(path, backup)
        assert backup.exists()
        before = path.stat().st_size
        await compact_database(path)
        assert path.stat().st_size <= before
