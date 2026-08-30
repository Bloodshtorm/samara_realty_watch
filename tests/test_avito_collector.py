from collectors.avito import _page_url
from collectors.html_extract import parsed_from_avito_cards


def test_page_url_adds_or_replaces_page_param() -> None:
    url = "https://www.avito.ru/samara/kvartiry/prodam-ASgBAgICAUSSA8YQ?context=abc"
    assert _page_url(url, 12) == (
        "https://www.avito.ru/samara/kvartiry/prodam-ASgBAgICAUSSA8YQ?context=abc&p=12"
    )
    assert _page_url(f"{url}&p=2", 13) == (
        "https://www.avito.ru/samara/kvartiry/prodam-ASgBAgICAUSSA8YQ?context=abc&p=13"
    )


def test_avito_land_cards_do_not_require_rooms() -> None:
    html = """
    <div data-marker="item">
      <a data-marker="item-title" href="/samara/zemelnye_uchastki/uchastok_10_sot._123456">
        Участок 10 сот.
      </a>
      <span>1 500 000 ₽</span>
      <span>Самара, Красноглинский район</span>
    </div>
    """

    listings = parsed_from_avito_cards(
        "avito",
        html,
        "https://www.avito.ru/samara/zemelnye_uchastki",
        property_type="land",
        require_rooms=False,
    )

    assert len(listings) == 1
    assert listings[0].property_type == "land"
    assert listings[0].rooms is None
    assert listings[0].area_total_m2 == 1000
