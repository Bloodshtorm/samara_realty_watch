from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class MarketStats:
    median_price_per_m2: int | None
    comparable_count: int


@dataclass(frozen=True)
class ScoreResult:
    score: int
    score_details: dict[str, int | str]
    reasons: list[str]


def score_listing(
    *,
    price_per_m2: int | None,
    area_total_m2: float | None,
    first_seen_at: datetime | None,
    features: dict[str, bool | None],
    market: MarketStats,
    config: dict,
    price_drop_rub: int | None = None,
    price_drop_percent: float | None = None,
) -> ScoreResult:
    weights = config.get("weights", {})
    thresholds = config.get("thresholds", {})
    target = config.get("global", {})
    details: dict[str, int | str] = {}
    reasons: list[str] = []
    score = 0

    if price_per_m2 and market.median_price_per_m2 and market.comparable_count >= 5:
        discount = (market.median_price_per_m2 - price_per_m2) / market.median_price_per_m2 * 100
        if discount >= thresholds.get("attractive_discount_percent", 8):
            points = round(weights.get("price_vs_market", 35) * min(discount / 15, 1))
            details["price_vs_market"] = points
            score += points
            reasons.append(f"Цена за м2 на {discount:.1f}% ниже медианы сопоставимых объектов")
    else:
        details["market_confidence"] = "low"

    if price_drop_rub and price_drop_percent:
        strong_rub = thresholds.get("strong_price_drop_rub", 250000)
        strong_pct = thresholds.get("strong_price_drop_percent", 3)
        if price_drop_rub >= strong_rub or price_drop_percent >= strong_pct:
            points = weights.get("price_reduction", 15)
            details["price_reduction"] = points
            score += points
            reasons.append(f"Цена снижена на {price_drop_rub:,} руб.".replace(",", " "))

    if first_seen_at:
        if first_seen_at.tzinfo is None:
            first_seen_at = first_seen_at.replace(tzinfo=UTC)
        age_hours = (datetime.now(UTC) - first_seen_at).total_seconds() / 3600
        if age_hours <= 24:
            points = weights.get("freshness", 10)
            details["freshness"] = points
            score += points
            reasons.append("Объект опубликован менее суток назад")

    if area_total_m2:
        min_area = target.get("target_area_min_m2", 60)
        max_area = target.get("target_area_max_m2", 100)
        if min_area <= area_total_m2 <= max_area:
            points = weights.get("area_fit", 10)
            details["area_fit"] = points
            score += points

    mortgage_hits = [name for name in ("it_mortgage", "family_mortgage") if features.get(name)]
    if mortgage_hits:
        points = weights.get("mortgage_fit", 10)
        details["mortgage_fit"] = points
        score += points
        reasons.append("В объявлении указана льготная ипотека")

    return ScoreResult(score=min(score, 100), score_details=details, reasons=reasons)
