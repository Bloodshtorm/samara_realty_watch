from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from app.models import Listing


@dataclass(frozen=True)
class DuplicateCandidate:
    confidence: float
    match_reason: dict[str, float | bool | str | None]


def compare_listings(a: Listing, b: Listing) -> DuplicateCandidate | None:
    if a.id == b.id:
        return None
    reason: dict[str, float | bool | str | None] = {}
    score = 0.0
    weight = 0.0

    address_score = 0.0
    if a.address_normalized and b.address_normalized:
        address_score = fuzz.token_set_ratio(a.address_normalized, b.address_normalized) / 100
        score += address_score * 0.35
        weight += 0.35
    reason["address_similarity"] = round(address_score, 3)

    if a.area_total_m2 and b.area_total_m2:
        area_close = abs(float(a.area_total_m2) - float(b.area_total_m2)) <= 1
        score += (1.0 if area_close else 0.0) * 0.2
        weight += 0.2
        reason["area_close"] = area_close

    for field, part_weight in (("rooms", 0.1), ("floor", 0.1), ("floors_total", 0.05)):
        av = getattr(a, field)
        bv = getattr(b, field)
        if av is not None and bv is not None:
            equal = av == bv
            score += (1.0 if equal else 0.0) * part_weight
            weight += part_weight
            reason[f"{field}_equal"] = equal

    if a.price_rub and b.price_rub:
        price_close = abs(a.price_rub - b.price_rub) / max(a.price_rub, b.price_rub) <= 0.05
        score += (1.0 if price_close else 0.0) * 0.1
        weight += 0.1
        reason["price_close"] = price_close

    text_a = " ".join(x for x in (a.title, a.description) if x)
    text_b = " ".join(x for x in (b.title, b.description) if x)
    if text_a and text_b:
        text_score = fuzz.token_set_ratio(text_a, text_b) / 100
        score += text_score * 0.1
        weight += 0.1
        reason["text_similarity"] = round(text_score, 3)

    confidence = score / weight if weight else 0.0
    if confidence >= 0.75:
        reason["match_type"] = (
            "same_source_duplicate" if a.source == b.source else "cross_source_probable_duplicate"
        )
        return DuplicateCandidate(confidence=round(confidence, 3), match_reason=reason)
    return None
