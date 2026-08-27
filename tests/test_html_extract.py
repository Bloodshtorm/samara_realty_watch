from pathlib import Path

from collectors.html_extract import parsed_from_json_ld


def test_yandex_fixture_json_ld() -> None:
    html = Path("tests/fixtures/yandex_sample.html").read_text(encoding="utf-8")
    listings = parsed_from_json_ld("yandex_realty", html, "https://realty.yandex.ru")
    assert len(listings) == 1
    listing = listings[0]
    assert listing.price_rub == 8_900_000
    assert listing.area_total_m2 == 73.2
    assert listing.floor == 7
    assert listing.floors_total == 16
    assert listing.features["it_mortgage"] is True
