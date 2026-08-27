from pathlib import Path

from collectors.html_extract import parsed_from_json_ld, parsed_from_offer_links


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


def test_offer_links_fallback() -> None:
    html = """
    <article>
      <a href="/offer/1537367160041063425/">
        95 м² · 3-комнатная квартира · 1 этаж из 17
        улица Клары Цеткин, 23А Продаётся квартира 18 000 000 ₽ 189 474 ₽ за м²
      </a>
      <a href="/offer/1537367160041063425/">от 144 630 ₽ в месяц</a>
    </article>
    """
    listings = parsed_from_offer_links("yandex_realty", html, "https://realty.yandex.ru/samara/")
    assert len(listings) == 1
    assert listings[0].source_listing_id == "1537367160041063425"
    assert listings[0].area_total_m2 == 95
    assert listings[0].rooms == 3
    assert listings[0].floor == 1
    assert listings[0].floors_total == 17
    assert listings[0].price_rub == 18_000_000
    assert listings[0].price_per_m2 == 189_474
    assert listings[0].address_normalized == "улица клары цеткин, 23а"


def test_offer_links_price_after_building_quarter() -> None:
    html = """
    <li data-test="OffersSerpItem">
      <a href="/offer/7567071399375559488/">
        92,4 м² · 3-комнатная квартира · 7 этаж из 16
        Самара, жилой комплекс Горизонт ЖК «Горизонт 2», 3 квартал 2026
        11 350 000 ₽ от 132 976 ₽ в месяц
      </a>
    </li>
    """
    listing = parsed_from_offer_links(
        "yandex_realty",
        html,
        "https://realty.yandex.ru/samara/",
    )[0]
    assert listing.price_rub == 11_350_000
