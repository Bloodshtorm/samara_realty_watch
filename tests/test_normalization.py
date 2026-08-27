from services.normalization import (
    calc_price_per_m2,
    normalize_address,
    parse_area_m2,
    parse_floor,
    parse_price_rub,
)


def test_parse_russian_price_formats() -> None:
    assert parse_price_rub("8 900 000 ₽") == 8_900_000
    assert parse_price_rub("12\u00a0340\u00a0000 руб.") == 12_340_000


def test_parse_area_formats() -> None:
    assert parse_area_m2("73,2 м²") == 73.2
    assert parse_area_m2("81.5 кв. м") == 81.5


def test_parse_floor() -> None:
    assert parse_floor("7/16 этаж") == (7, 16)


def test_calc_price_per_m2() -> None:
    assert calc_price_per_m2(8_900_000, 73.2) == 121_585


def test_normalize_address() -> None:
    assert normalize_address("г. Самара, ул. Ново-Садовая, д. 100") == (
        "самара, улица ново-садовая, дом 100"
    )
