from __future__ import annotations

import math

from app.models import Listing, SearchContext
from services.normalization import is_outside_samara_city_listing, is_wrong_room_count


def valid_coordinates(lat: float | None, lng: float | None) -> bool:
    return (
        lat is not None
        and lng is not None
        and math.isfinite(lat)
        and math.isfinite(lng)
        and -90 <= lat <= 90
        and -180 <= lng <= 180
        and (lat, lng) != (0, 0)
    )


def usable_coordinates(lat: float | None, lng: float | None) -> bool:
    # Avito's defaultCoords is a city-map fallback, not a property position.
    return valid_coordinates(lat, lng) and (lat, lng) != (53.195538, 50.101783)


def distance_km(lat: float, lng: float, center_lat: float, center_lng: float) -> float:
    a, b = math.radians(lat), math.radians(center_lat)
    h = (
        math.sin((a - b) / 2) ** 2
        + math.cos(a) * math.cos(b) * math.sin(math.radians(lng - center_lng) / 2) ** 2
    )
    return 12742 * math.asin(math.sqrt(min(1, max(0, h))))


def in_context(listing: Listing, context: SearchContext, location: str = "within") -> bool:
    if context.object_type == "flat":
        if is_outside_samara_city_listing(listing.address_raw, listing.address_normalized):
            return False
        if listing.floors_total is not None and listing.floors_total <= 5:
            return False
        if context.expected_rooms is not None and is_wrong_room_count(
            listing.rooms, context.expected_rooms, listing.title
        ):
            return False
    if context.radius_km is None:
        return True
    known = usable_coordinates(listing.latitude, listing.longitude)
    if location == "unknown":
        return not known
    if not known or context.center_latitude is None or context.center_longitude is None:
        return False
    assert listing.latitude is not None and listing.longitude is not None
    return (
        distance_km(
            listing.latitude, listing.longitude, context.center_latitude, context.center_longitude
        )
        <= context.radius_km
    )
