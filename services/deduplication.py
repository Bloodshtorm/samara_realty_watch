from __future__ import annotations

import math
import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from app.models import Listing
from services.normalization import compact_text

ALGORITHM_VERSION = "apartments-v1"
FLAT_TYPES = {"flat", "flats", "flatSale", "newBuildingFlatSale", "apartment", "квартира"}


@dataclass(frozen=True)
class DuplicateCandidate:
    confidence: float
    match_reason: dict
    automatic: bool = False


def is_flat(item: Listing) -> bool:
    if item.property_type:
        return item.property_type in FLAT_TYPES
    title = (item.title or "").lower()
    return bool(
        re.search(r"\bквартира\b", title)
        and not re.search(r"\b(?:доля|доли|комната|комнаты)\b", title)
        and item.rooms is not None
        and item.floor is not None
    )


def building_key(item: Listing) -> str | None:
    """Accept an explicit street/house pair, preserving corpus and building suffixes."""
    text = (compact_text(item.address_normalized or item.address_raw) or "").lower()
    if not text or "самара" not in text:
        return None
    text = re.sub(r"\b(?:дом|д\.)\s*", "", text)
    text = re.sub(r"\s*(?:корпус|корп\.?|к\.)\s*", "к", text)
    text = re.sub(r"\s*(?:строение|стр\.)\s*", "с", text)
    text = re.sub(r"\bул\.\s*", "улица ", text)
    match = re.search(r"(?:^|,)\s*([^,]+?)\s*,?\s+(\d+[а-яa-z]?(?:[/кс-]\d+[а-яa-z]?)*)\s*$", text)
    if not match:
        return None
    street, house = match.groups()
    street = re.sub(r"^самара\s+", "", street)
    street = re.sub(r"\bулица\b", "", street).strip()
    if not street or any(token in street for token in ("район", "область", "самара", "метро")):
        return None
    return f"самара|{street}|{house}"


def distance_m(a: Listing, b: Listing) -> float | None:
    if a.latitude is None or a.longitude is None or b.latitude is None or b.longitude is None:
        return None
    lat_a, lng_a, lat_b, lng_b = map(
        math.radians, (a.latitude, a.longitude, b.latitude, b.longitude)
    )
    value = (
        math.sin((lat_b - lat_a) / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin((lng_b - lng_a) / 2) ** 2
    )
    return 6_371_000 * 2 * math.asin(min(1, math.sqrt(value)))


def photo_ids(item: Listing) -> set[str]:
    raw = item.raw_payload or {}
    result: set[str] = set()
    photos = raw.get("photos", [])
    if not isinstance(photos, list):
        return result
    for photo in photos:
        if not isinstance(photo, dict):
            continue
        if item.source == "cian" and photo.get("id"):
            result.add(f"cian:{photo['id']}")
        elif item.source == "n1":
            match = re.fullmatch(r"/cian/(\d+)\.jpg", str(photo.get("original", "")))
            if match:
                result.add(f"cian:{match.group(1)}")
    return result


def description_text(item: Listing) -> str:
    return re.sub(
        r"\s+", " ", re.sub(r"[^\w\s]", " ", (compact_text(item.description) or "").lower())
    ).strip()


def specific_description(text: str) -> bool:
    # Generic sales templates are not apartment-specific evidence.
    return len(set(text.split())) >= 35 and bool(
        re.search(r"\bномер объекта\s+\d+\b", text)
        or re.search(r"\b\d+\s+\d+\s+(?:кв|м2|м²)\b", text)
    )


def compare_listings(a: Listing, b: Listing) -> DuplicateCandidate | None:
    if a.id == b.id or not is_flat(a) or not is_flat(b):
        return None
    key_a, key_b = building_key(a), building_key(b)
    same_house = bool(key_a and key_a == key_b)
    distance = distance_m(a, b)
    if not same_house and (distance is None or distance > 50):
        return None
    conflicts = [
        field
        for field in ("rooms", "floor", "floors_total")
        if getattr(a, field) is not None
        and getattr(b, field) is not None
        and getattr(a, field) != getattr(b, field)
    ]
    if key_a and key_b and key_a != key_b:
        conflicts.append("address")
    area_close = bool(
        a.area_total_m2
        and b.area_total_m2
        and abs(float(a.area_total_m2) - float(b.area_total_m2))
        <= max(1, max(float(a.area_total_m2), float(b.area_total_m2)) * 0.02)
    )
    mandatory = (
        same_house
        and not conflicts
        and area_close
        and all(
            getattr(a, field) is not None and getattr(a, field) == getattr(b, field)
            for field in ("rooms", "floor")
        )
    )
    price_close = bool(
        a.price_rub
        and b.price_rub
        and abs(a.price_rub - b.price_rub) / max(a.price_rub, b.price_rub) <= 0.05
    )
    text_a, text_b = description_text(a), description_text(b)
    text_score = fuzz.ratio(text_a, text_b) / 100 if min(len(text_a), len(text_b)) >= 200 else 0.0
    shared_photos = len(photo_ids(a) & photo_ids(b))
    automatic = bool(
        mandatory
        and (
            shared_photos >= 2
            or (
                text_score >= 0.92
                and price_close
                and specific_description(text_a)
                and specific_description(text_b)
            )
        )
    )
    reason = {
        "algorithm_version": ALGORITHM_VERSION,
        "same_house": same_house,
        "distance_m": round(distance, 1) if distance is not None else None,
        "area_close": area_close,
        "price_close": price_close,
        "description_similarity": round(text_score, 3),
        "shared_photos": shared_photos,
        "conflicts": conflicts,
        "automatic": automatic,
        "match_type": "cross_source_probable_duplicate",
    }
    if not automatic and not (
        shared_photos >= 2 or text_score >= 0.7 or (mandatory and price_close)
    ):
        return None
    return DuplicateCandidate(1.0 if automatic else 0.5, reason, automatic)
