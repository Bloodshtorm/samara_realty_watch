from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

FEATURE_NAMES = (
    "mortgage_available",
    "family_mortgage",
    "it_mortgage",
    "subsidized_mortgage",
    "installment_available",
    "new_building",
    "secondary_market",
    "renovation_required",
    "renovated",
    "balcony",
    "loggia",
    "elevator",
    "parking",
    "owner_sale",
    "agency_sale",
    "bargain_possible",
    "alternative_deal",
    "encumbrance_mentioned",
    "power_of_attorney_mentioned",
    "redevelopment_mentioned",
)


def default_features() -> dict[str, bool | None]:
    return {name: None for name in FEATURE_NAMES}


class ParsedListing(BaseModel):
    source: str
    source_listing_id: str
    url: str
    canonical_url: str
    title: str | None = None
    address_raw: str | None = None
    address_normalized: str | None = None
    district: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    property_type: str | None = None
    seller_type: str | None = "unknown"
    rooms: int | None = None
    area_total_m2: float | None = None
    area_living_m2: float | None = None
    area_kitchen_m2: float | None = None
    price_rub: int | None = None
    price_per_m2: int | None = None
    floor: int | None = None
    floors_total: int | None = None
    building_year: int | None = None
    building_type: str | None = None
    description: str | None = None
    phone_masked: str | None = None
    photos_count: int | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    features: dict[str, bool | None] = Field(default_factory=default_features)
