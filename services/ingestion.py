from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Listing, ListingObservation, PriceHistory, Search
from app.schemas import ParsedListing
from services.geography import usable_coordinates
from services.normalization import (
    calc_price_per_m2,
    detect_district,
    mortgage_features,
    normalize_address,
)


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
    description_changed = listing is None or listing.description != parsed.description

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
    old_address = normalize_address(listing.address_raw or listing.address_normalized)
    new_address = normalize_address(parsed.address_raw or parsed.address_normalized)
    inferred = bool((listing.features or {}).get("coordinates_inferred"))
    if usable_coordinates(parsed.latitude, parsed.longitude):
        listing.latitude, listing.longitude = parsed.latitude, parsed.longitude
        inferred = False
    elif created or (new_address and old_address != new_address):
        listing.latitude = listing.longitude = None
        inferred = False
    for field in (
        "url",
        "canonical_url",
        "title",
        "address_raw",
        "address_normalized",
        "district",
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
        if field in {"address_raw", "address_normalized"} and getattr(parsed, field) is None:
            continue
        setattr(listing, field, getattr(parsed, field))
    listing.district = detect_district(listing.address_raw) or (
        parsed.district if parsed.district != "самарский" else None
    )
    listing.features = {
        **(parsed.features or {}),
        **mortgage_features(" ".join(filter(None, [parsed.title, parsed.description]))),
    }
    if inferred:
        listing.features["coordinates_inferred"] = True
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
            description_snapshot=parsed.description if description_changed else None,
            # Keep source JSON only on the current listing, never on every observation.
            raw_payload=None,
        )
    )
    await session.flush()
    return IngestResult(
        listing=listing, created=created, updated=not created, price_changed=price_changed
    )
