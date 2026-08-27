import pytest
from fastapi import HTTPException

from app.web import parse_filters


def test_parse_filters_treats_empty_form_values_as_none() -> None:
    filters = parse_filters(
        price_min="",
        price_max="",
        price_m2_max="",
        area_min="",
        area_max="",
        floor_min="",
        floor_max="",
        floors_total_max="",
        district="",
        source="mirkvartir",
        changed_days="",
        seen_days="7",
        sort="price",
    )

    assert filters.price_min is None
    assert filters.area_min is None
    assert filters.changed_days is None
    assert filters.source == "mirkvartir"
    assert filters.seen_days == 7


def test_parse_filters_parses_numeric_values() -> None:
    filters = parse_filters(
        price_min="5000000",
        price_max=None,
        price_m2_max=None,
        area_min="65,5",
        area_max=None,
        floor_min=None,
        floor_max=None,
        floors_total_max=None,
        district=None,
        source=None,
        changed_days=None,
        seen_days="30",
        sort="price",
    )

    assert filters.price_min == 5_000_000
    assert filters.area_min == 65.5
    assert filters.seen_days == 30


def test_parse_filters_rejects_invalid_numeric_values() -> None:
    with pytest.raises(HTTPException):
        parse_filters(price_min="wrong")
