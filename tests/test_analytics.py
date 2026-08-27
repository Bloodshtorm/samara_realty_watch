from datetime import UTC, datetime

from app.models import Listing
from services.analytics import (
    ListingHistoryStats,
    area_bucket,
    build_market_segments,
    recommend_listing,
    segment_key,
)


def test_area_bucket() -> None:
    assert area_bucket(None) == "unknown"
    assert area_bucket(59.9) == "<60"
    assert area_bucket(70) == "60-75"
    assert area_bucket(80) == "75-90"
    assert area_bucket(100) == "90+"


def test_recommendation_rewards_discount_to_segment() -> None:
    cheap = Listing(
        source="yandex_realty",
        source_listing_id="1",
        url="https://example.test/1",
        canonical_url="https://example.test/1",
        district="советский",
        area_total_m2=70,
        price_rub=7_000_000,
        price_per_m2=100_000,
        floor=5,
        floors_total=12,
        first_seen_at=datetime.now(UTC),
    )
    expensive = Listing(
        source="yandex_realty",
        source_listing_id="2",
        url="https://example.test/2",
        canonical_url="https://example.test/2",
        district="советский",
        area_total_m2=71,
        price_rub=10_650_000,
        price_per_m2=150_000,
    )
    segments = build_market_segments([cheap, expensive])
    recommendation = recommend_listing(
        cheap,
        ListingHistoryStats(
            observations_count=3,
            first_price_rub=7_200_000,
            current_price_rub=7_000_000,
        ),
        segments[segment_key(cheap)],
    )

    assert recommendation.score >= 80
    assert any("ниже сегмента" in reason for reason in recommendation.reasons)
    assert any("Цена снижалась" in reason for reason in recommendation.reasons)
