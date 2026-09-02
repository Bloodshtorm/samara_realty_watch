from collectors.avito import _page_url
from collectors.html_extract import parsed_from_avito_cards, parsed_from_avito_detail


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


def test_avito_dacha_cards_fit_land_context() -> None:
    html = """
    <div data-marker="item">
      <a
        data-marker="item-title"
        href="/samara/doma_dachi_kottedzhi/dacha_191_m_na_uchastke_7_sot._8250141503"
      >
        Дача 191 м² на участке 7 сот.
      </a>
      <span>1 200 000 ₽</span>
      <span>Самара, Красноглинский район</span>
    </div>
    """

    listings = parsed_from_avito_cards(
        "avito",
        html,
        "https://www.avito.ru/samara/doma_dachi_kottedzhi/prodam/dachi-ASgBAgICAUSUA9AQ",
        property_type="land",
        require_rooms=False,
    )

    assert len(listings) == 1
    assert listings[0].source_listing_id == "8250141503"
    assert listings[0].property_type == "land"
    assert listings[0].rooms is None
    assert listings[0].area_total_m2 == 700


def test_avito_detail_page_can_be_imported_for_land_context() -> None:
    html = """
    <html>
      <head>
        <meta property="product:price:amount" content="3700000">
        <meta
          name="description"
          content="Продается уютный летний кирпичный дом. Возможна ипотека."
        >
      </head>
      <body>
        <h1 data-marker="item-view/title-info">Дача 19,1 м² на участке 7 сот.</h1>
        <div>
          Расположение Самарская обл., Самара, СНТ Ракитовские Дачи-1,
          20-я ул., 10 р-н Красноглинский Скрыть карту
        </div>
        <script>
          window.__data = "{\\"geoMap\\":{\\"params\\":{\\"defaultCoords\\":{
            \\"latitude\\":53.195538,\\"longitude\\":50.101783,\\"zoom\\":16
          }}}}";
        </script>
      </body>
    </html>
    """

    listings = parsed_from_avito_detail(
        "avito",
        html,
        (
            "https://www.avito.ru/samara/doma_dachi_kottedzhi/"
            "dacha_191_m_na_uchastke_7_sot._8250141503"
        ),
        property_type="land",
    )

    assert len(listings) == 1
    assert listings[0].source_listing_id == "8250141503"
    assert listings[0].price_rub == 3_700_000
    assert listings[0].area_total_m2 == 700
    assert listings[0].latitude == 53.195538
    assert listings[0].longitude == 50.101783
    assert listings[0].district == "красноглинский"
