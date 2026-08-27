from datetime import UTC, datetime
from uuid import uuid4

from app.models import Listing
from services.deduplication import compare_listings
from services.scoring import MarketStats, score_listing


def listing(**kwargs) -> Listing:
    defaults = {
        "id": uuid4(),
        "source": "a",
        "source_listing_id": str(uuid4()),
        "url": "https://example.test/1",
        "canonical_url": "https://example.test/1",
        "address_normalized": "самара улица ново-садовая дом 100",
        "rooms": 3,
        "area_total_m2": 73.2,
        "floor": 7,
        "floors_total": 16,
        "price_rub": 8_900_000,
        "title": "3-комнатная квартира",
        "description": "хорошая квартира с лоджией",
    }
    defaults.update(kwargs)
    return Listing(**defaults)


def test_deduplication_probable_match() -> None:
    a = listing(source="yandex_realty")
    b = listing(source="cian", price_rub=9_000_000, canonical_url="https://example.test/2")
    candidate = compare_listings(a, b)
    assert candidate is not None
    assert candidate.confidence >= 0.75
    assert candidate.match_reason["match_type"] == "cross_source_probable_duplicate"


def test_scoring_below_median() -> None:
    result = score_listing(
        price_per_m2=100_000,
        area_total_m2=73.2,
        first_seen_at=datetime.now(UTC),
        features={"it_mortgage": True},
        market=MarketStats(median_price_per_m2=115_000, comparable_count=12),
        config={
            "weights": {"price_vs_market": 35, "freshness": 10, "area_fit": 10, "mortgage_fit": 10},
            "thresholds": {"attractive_discount_percent": 8},
            "global": {"target_area_min_m2": 60, "target_area_max_m2": 100},
        },
    )
    assert result.score >= 55
    assert "price_vs_market" in result.score_details
