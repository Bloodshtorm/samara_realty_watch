from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Listing, ListingObservation, PriceHistory, Search
from app.schemas import ParsedListing
from services.normalization import calc_price_per_m2


@dataclass(frozen=True)
class IngestResult:
    listing: Listing
    created: bool
    updated: bool
    price_changed: bool


async def upsert_listing(
    session: AsyncSession, search: Search, parsed: ParsedListing
) -> IngestResult:
    now = datetime.now(UTC)
    result = await session.execute(
        select(Listing).where(
            Listing.source == parsed.source,
            Listing.source_listing_id == parsed.source_listing_id,
        )
    )
    listing = result.scalar_one_or_none()
    created = listing is None
    price_changed = False
    old_price = None

    if created:
        listing = Listing(
            source=parsed.source,
            source_listing_id=parsed.source_listing_id,
            url=parsed.url,
            canonical_url=parsed.canonical_url,
            first_seen_at=now,
            last_seen_at=now,
            last_active_at=now,
            is_active=True,
        )
        session.add(listing)
    else:
        assert listing is not None
        old_price = listing.price_rub
        listing.last_seen_at = now
        listing.last_active_at = now
        listing.is_active = True

    area = parsed.area_total_m2
    price_per_m2 = parsed.price_per_m2 or calc_price_per_m2(parsed.price_rub, area)
    for field in (
        "url",
        "canonical_url",
        "title",
        "address_raw",
        "address_normalized",
        "district",
        "latitude",
        "longitude",
        "property_type",
        "seller_type",
        "rooms",
        "area_total_m2",
        "area_living_m2",
        "area_kitchen_m2",
        "price_rub",
        "floor",
        "floors_total",
        "building_year",
        "building_type",
        "description",
        "phone_masked",
        "photos_count",
        "raw_payload",
        "features",
    ):
        setattr(listing, field, getattr(parsed, field))
    listing.price_per_m2 = price_per_m2

    if not created and old_price != parsed.price_rub and parsed.price_rub is not None:
        price_changed = True
        session.add(
            PriceHistory(
                listing_id=listing.id,
                observed_at=now,
                old_price_rub=old_price,
                new_price_rub=parsed.price_rub,
                change_rub=None if old_price is None else parsed.price_rub - old_price,
                change_percent=None
                if old_price in (None, 0)
                else round((parsed.price_rub - old_price) / old_price * 100, 4),
            )
        )

    session.add(
        ListingObservation(
            listing=listing,
            search_id=search.id,
            observed_at=now,
            price_rub=parsed.price_rub,
            price_per_m2=price_per_m2,
            is_active=True,
            title_snapshot=parsed.title,
            description_snapshot=parsed.description,
            raw_payload=parsed.raw_payload,
        )
    )
    await session.flush()
    return IngestResult(
        listing=listing, created=created, updated=not created, price_changed=price_changed
    )
