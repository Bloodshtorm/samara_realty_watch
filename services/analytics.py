from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from statistics import median
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Listing, ListingObservation, PriceHistory


@dataclass(frozen=True)
class SegmentKey:
    district: str
    area_bucket: str


@dataclass(frozen=True)
class SegmentStats:
    key: SegmentKey
    comparable_count: int
    median_price_rub: int | None
    median_price_per_m2: int | None


@dataclass
class ListingHistoryStats:
    observations_count: int = 0
    price_changes_count: int = 0
    first_price_rub: int | None = None
    min_price_rub: int | None = None
    max_price_rub: int | None = None
    current_price_rub: int | None = None
    total_change_rub: int | None = None
    total_change_percent: float | None = None
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None


@dataclass(frozen=True)
class ListingRecommendation:
    score: int
    segment: SegmentStats | None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def segment_key(listing: Listing) -> SegmentKey:
    return SegmentKey(
        district=listing.district or "unknown",
        area_bucket=area_bucket(float(listing.area_total_m2) if listing.area_total_m2 else None),
    )


def area_bucket(area: float | None) -> str:
    if area is None:
        return "unknown"
    if area < 60:
        return "<60"
    if area < 75:
        return "60-75"
    if area < 90:
        return "75-90"
    return "90+"


def build_market_segments(listings: list[Listing]) -> dict[SegmentKey, SegmentStats]:
    groups: dict[SegmentKey, list[Listing]] = defaultdict(list)
    for listing in listings:
        groups[segment_key(listing)].append(listing)

    result: dict[SegmentKey, SegmentStats] = {}
    for key, group in groups.items():
        prices = [item.price_rub for item in group if item.price_rub is not None]
        prices_m2 = [item.price_per_m2 for item in group if item.price_per_m2 is not None]
        result[key] = SegmentStats(
            key=key,
            comparable_count=len(group),
            median_price_rub=int(median(prices)) if prices else None,
            median_price_per_m2=int(median(prices_m2)) if prices_m2 else None,
        )
    return result


async def listing_history_stats(
    session: AsyncSession,
    listing_ids: list[UUID],
) -> dict[UUID, ListingHistoryStats]:
    if not listing_ids:
        return {}

    stats = {listing_id: ListingHistoryStats() for listing_id in listing_ids}
    observations = (
        await session.execute(
            select(ListingObservation)
            .where(ListingObservation.listing_id.in_(listing_ids))
            .order_by(ListingObservation.listing_id, ListingObservation.observed_at)
        )
    ).scalars().all()
    for observation in observations:
        item = stats[observation.listing_id]
        item.observations_count += 1
        if item.first_observed_at is None:
            item.first_observed_at = observation.observed_at
            item.first_price_rub = observation.price_rub
        item.last_observed_at = observation.observed_at
        item.current_price_rub = observation.price_rub
        if observation.price_rub is None:
            continue
        if item.min_price_rub is None or observation.price_rub < item.min_price_rub:
            item.min_price_rub = observation.price_rub
        if item.max_price_rub is None or observation.price_rub > item.max_price_rub:
            item.max_price_rub = observation.price_rub

    change_counts = await session.execute(
        select(PriceHistory.listing_id, func.count())
        .where(PriceHistory.listing_id.in_(listing_ids))
        .group_by(PriceHistory.listing_id)
    )
    for listing_id, count in change_counts.all():
        stats[listing_id].price_changes_count = count

    for item in stats.values():
        if item.first_price_rub is None or item.current_price_rub is None:
            continue
        item.total_change_rub = item.current_price_rub - item.first_price_rub
        if item.first_price_rub:
            item.total_change_percent = item.total_change_rub / item.first_price_rub * 100

    return stats


def recommend_listing(
    listing: Listing,
    history: ListingHistoryStats,
    segment: SegmentStats | None,
) -> ListingRecommendation:
    score = 40
    reasons: list[str] = []
    warnings: list[str] = []

    if listing.price_per_m2 and segment and segment.median_price_per_m2:
        diff = (segment.median_price_per_m2 - listing.price_per_m2) / segment.median_price_per_m2
        if diff >= 0.12:
            score += 30
            reasons.append(f"Цена за м² ниже сегмента на {diff * 100:.1f}%")
        elif diff >= 0.06:
            score += 18
            reasons.append(f"Цена за м² ниже сегмента на {diff * 100:.1f}%")
        elif diff <= -0.12:
            score -= 25
            warnings.append(f"Цена за м² выше сегмента на {abs(diff) * 100:.1f}%")
        elif diff <= -0.06:
            score -= 12
            warnings.append(f"Цена за м² выше сегмента на {abs(diff) * 100:.1f}%")
    else:
        warnings.append("Недостаточно данных для сравнения с сегментом")

    if listing.area_total_m2:
        area = float(listing.area_total_m2)
        if 60 <= area <= 95:
            score += 10
            reasons.append("Площадь в рабочем диапазоне 60-95 м²")
        elif area < 55:
            score -= 8
            warnings.append("Маленькая площадь для 3-комнатной квартиры")

    if listing.floor and listing.floors_total:
        if listing.floor == 1:
            score -= 10
            warnings.append("Первый этаж")
        elif listing.floor == listing.floors_total:
            score -= 7
            warnings.append("Последний этаж")
        else:
            score += 5
            reasons.append("Этаж не первый и не последний")
        if listing.floors_total > 24:
            score -= 5
            warnings.append("Высокая этажность дома")

    total_change_rub = history.total_change_rub
    if (
        total_change_rub is None
        and history.first_price_rub is not None
        and history.current_price_rub is not None
    ):
        total_change_rub = history.current_price_rub - history.first_price_rub

    if total_change_rub is not None:
        if total_change_rub < 0:
            score += 12
            reasons.append("Цена снижалась с первого наблюдения")
        elif total_change_rub > 0:
            score -= 8
            warnings.append("Цена выросла с первого наблюдения")

    if listing.first_seen_at:
        first_seen_at = listing.first_seen_at
        if first_seen_at.tzinfo is None:
            first_seen_at = first_seen_at.replace(tzinfo=UTC)
        age_days = (datetime.now(UTC) - first_seen_at).days
        if age_days <= 3:
            score += 5
            reasons.append("Новое объявление")
        elif age_days >= 45:
            score += 5
            reasons.append("Долго висит, возможен торг")

    if history.observations_count >= 3:
        score += 3
        reasons.append("Есть несколько наблюдений для проверки динамики")

    return ListingRecommendation(
        score=max(0, min(score, 100)),
        segment=segment,
        reasons=reasons[:4],
        warnings=warnings[:4],
    )
