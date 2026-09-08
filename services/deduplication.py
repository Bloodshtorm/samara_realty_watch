from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal

from rapidfuzz import fuzz

from app.models import Listing
from services.normalization import compact_text

ALGORITHM_VERSION = "apartments-v2"
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
    if not text:
        return None
    text = re.sub(r"\b(?:дом|д\.)\s*", "", text)
    text = re.sub(r"\s*(?:корпус|корп\.?|к\.)\s*", "к", text)
    text = re.sub(r"\s*(?:строение|стр\.)\s*", "с", text)
    text = re.sub(r"\bул\.\s*", "улица ", text)
    matches = list(
        re.finditer(
            r"(?:^|,)\s*([^,]+?)\s*,?\s+(\d+[а-яa-z]?(?:[/кс-]\d+[а-яa-z]?)*)\b",
            text,
        )
    )
    street = house = None
    for match in reversed(matches):
        street, house = match.groups()
        if not any(token in street for token in ("район", "область", "самара", "метро", "мкр")):
            break
    if street is None or house is None:
        return None
    street = re.sub(r"^самара\s+", "", street)
    street = _canonical_street(street)
    if not street or any(token in street for token in ("район", "область", "самара", "метро")):
        return None
    return f"самара|{street}|{house}"


def _canonical_street(street: str) -> str:
    street = street.replace("-", " ")
    street = re.sub(r"\b(?:улица|проспект|просп|шоссе|бульвар|переулок|пер|проезд)\b", "", street)
    return re.sub(r"\s+", " ", street).strip()


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
    return diverse_description(text) and bool(
        agency_refs(text)
        or re.search(r"\b\d+\s+\d+\s+(?:кв|м2|м²)\b", text)
    )


def diverse_description(text: str) -> bool:
    return len(set(text.split())) >= 35


def agency_refs(text: str) -> set[str]:
    refs: set[str] = set()
    for pattern in (r"\bномер объекта\s+(\d{4,})\b", r"\bарт\s+(\d{4,})\b"):
        refs.update(re.findall(pattern, text))
    return refs


def description_mentions_area(text: str, area: Decimal | float | int | str | None) -> bool:
    if area is None:
        return False
    try:
        value = float(area)
    except ValueError:
        return False
    candidates = {f"{value:.1f}".replace(".", " ")}
    if value.is_integer():
        candidates.add(str(int(value)))
    return any(re.search(rf"\b{re.escape(candidate)}\b", text) for candidate in candidates)


def description_has_area_measurement(text: str) -> bool:
    return bool(
        re.search(r"\b\d{2,3}\s+\d\b", text)
        or re.search(r"\b\d{2,3}\s*(?:м2|м²|кв)\b", text)
    )


def compare_listings(a: Listing, b: Listing) -> DuplicateCandidate | None:
    if a.id == b.id or a.source == b.source or not is_flat(a) or not is_flat(b):
        return None
    key_a, key_b = building_key(a), building_key(b)
    same_house = bool(key_a and key_a == key_b)
    distance = distance_m(a, b)
    if not same_house and (distance is None or distance > 50):
        return None
    raw_conflicts = [
        field
        for field in ("rooms", "floor", "floors_total")
        if getattr(a, field) is not None
        and getattr(b, field) is not None
        and getattr(a, field) != getattr(b, field)
    ]
    if key_a and key_b and key_a != key_b:
        raw_conflicts.append("address")
    area_close = bool(
        a.area_total_m2
        and b.area_total_m2
        and abs(float(a.area_total_m2) - float(b.area_total_m2))
        <= max(1, max(float(a.area_total_m2), float(b.area_total_m2)) * 0.02)
    )
    price_close = bool(
        a.price_rub
        and b.price_rub
        and abs(a.price_rub - b.price_rub) / max(a.price_rub, b.price_rub) <= 0.05
    )
    text_a, text_b = description_text(a), description_text(b)
    text_score = fuzz.ratio(text_a, text_b) / 100 if min(len(text_a), len(text_b)) >= 200 else 0.0
    shared_refs = agency_refs(text_a) & agency_refs(text_b)
    shared_photos = len(photo_ids(a) & photo_ids(b))
    strong_identity_evidence = bool(shared_photos >= 2 or shared_refs)
    coordinate_confirmed_house = bool(
        not same_house
        and distance is not None
        and distance <= 15
        and strong_identity_evidence
    )
    conflicts = [
        conflict
        for conflict in raw_conflicts
        if not (conflict == "address" and coordinate_confirmed_house)
    ]
    area_text_override = bool(
        a.area_total_m2
        and b.area_total_m2
        and abs(float(a.area_total_m2) - float(b.area_total_m2))
        <= max(float(a.area_total_m2), float(b.area_total_m2)) * 0.1
        and price_close
        and text_score >= 0.96
        and (
            description_mentions_area(text_a, a.area_total_m2)
            or description_mentions_area(text_a, b.area_total_m2)
            or description_mentions_area(text_b, a.area_total_m2)
            or description_mentions_area(text_b, b.area_total_m2)
        )
    )
    mandatory = (
        (same_house or coordinate_confirmed_house)
        and not conflicts
        and (area_close or area_text_override)
        and all(
            getattr(a, field) is not None and getattr(a, field) == getattr(b, field)
            for field in ("rooms", "floor")
        )
    )
    specific_text_confirmation = (
        text_score >= 0.92
        and specific_description(text_a)
        and specific_description(text_b)
    )
    near_identical_text_confirmation = (
        text_score >= 0.96
        and diverse_description(text_a)
        and diverse_description(text_b)
    )
    exact_text_confirmation = (
        text_score >= 0.995
        and min(len(text_a), len(text_b)) >= 200
        and (
            description_mentions_area(text_a, a.area_total_m2)
            or description_mentions_area(text_a, b.area_total_m2)
            or description_mentions_area(text_b, a.area_total_m2)
            or description_mentions_area(text_b, b.area_total_m2)
            or description_has_area_measurement(text_a)
            or description_has_area_measurement(text_b)
        )
    )
    text_confirmation = bool(
        price_close
        and (
            shared_refs
            or specific_text_confirmation
            or near_identical_text_confirmation
            or (area_close and exact_text_confirmation)
        )
    )
    automatic = bool(
        mandatory
        and (
            shared_photos >= 2
            or text_confirmation
            or area_text_override
        )
    )
    reviewable = bool(
        shared_photos >= 2
        or (shared_refs and "floor" not in conflicts)
        or (
            not conflicts
            and area_close
            and price_close
            and text_score >= 0.7
        )
    )
    reason = {
        "algorithm_version": ALGORITHM_VERSION,
        "same_house": same_house,
        "coordinate_confirmed_house": coordinate_confirmed_house,
        "distance_m": round(distance, 1) if distance is not None else None,
        "area_close": area_close,
        "area_text_override": area_text_override,
        "price_close": price_close,
        "description_similarity": round(text_score, 3),
        "shared_photos": shared_photos,
        "shared_refs": sorted(shared_refs),
        "conflicts": conflicts,
        "automatic": automatic,
        "match_type": "cross_source_probable_duplicate",
    }
    if not automatic and not reviewable:
        return None
    return DuplicateCandidate(1.0 if automatic else 0.5, reason, automatic)
