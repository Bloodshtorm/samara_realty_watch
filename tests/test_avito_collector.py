import pytest

from app.models import Search
from collectors.avito import AvitoCollector, _page_url
from collectors.base import CollectorBlockedError
from collectors.html_extract import parsed_from_avito_cards, parsed_from_avito_detail


class _FakeLocator:
    def __init__(self, text: str) -> None:
        self._text = text

    async def inner_text(self, **kwargs: object) -> str:
        _ = kwargs
        return self._text


class _FakePage:
    url = "https://www.avito.ru/samara/kvartiry/prodam/3-komnatnye-ASgBAgICAUSSA8YQAkDmBxSM"

    def __init__(self, html: str, text: str = "") -> None:
        self._html = html
        self._text = text
        self.closed = False
        self.screenshot_saved = False

    async def goto(self, url: str, *, wait_until: str, **kwargs: object) -> None:
        _ = wait_until, kwargs
        self.url = url

    async def wait_for_timeout(self, timeout_ms: int) -> None:
        _ = timeout_ms
        return None

    async def wait_for_selector(self, selector: str, **kwargs: object) -> None:
        _ = selector, kwargs
        return None

    async def content(self) -> str:
        return self._html

    def locator(self, selector: str) -> _FakeLocator:
        _ = selector
        return _FakeLocator(self._text)

    async def screenshot(self, *, path: str, full_page: bool) -> None:
        _ = path, full_page
        self.screenshot_saved = True

    async def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self.page = page

    async def new_page(self) -> _FakePage:
        return self.page


def test_page_url_adds_or_replaces_page_param() -> None:
    url = "https://www.avito.ru/samara/kvartiry/prodam-ASgBAgICAUSSA8YQ?context=abc"
    assert _page_url(url, 12) == (
        "https://www.avito.ru/samara/kvartiry/prodam-ASgBAgICAUSSA8YQ?context=abc&p=12"
    )
    assert _page_url(f"{url}&p=2", 13) == (
        "https://www.avito.ru/samara/kvartiry/prodam-ASgBAgICAUSSA8YQ?context=abc&p=13"
    )


@pytest.mark.asyncio
async def test_avito_collector_fails_on_empty_first_page(tmp_path) -> None:
    page = _FakePage("<html><body>Авито — объявления</body></html>", "Авито — объявления")
    collector = AvitoCollector()
    collector.debug_run_id = "run-id"
    collector.debug_screenshots_dir = tmp_path / "screenshots"
    collector.debug_html_dir = tmp_path / "html"

    with pytest.raises(CollectorBlockedError, match="no parseable listings"):
        await collector.collect_search(
            Search(
                name="avito_samara_3rooms_secondary",
                source="avito",
                url=page.url,
                city="Самара",
                rooms=3,
                max_pages=1,
            ),
            _FakeContext(page),
        )

    assert page.closed is True
    assert collector.last_debug_html_path is not None


@pytest.mark.asyncio
async def test_avito_collector_fails_on_captcha_page(tmp_path) -> None:
    page = _FakePage("<html><body>captcha</body></html>", "captcha")
    collector = AvitoCollector()
    collector.debug_run_id = "run-id"
    collector.debug_screenshots_dir = tmp_path / "screenshots"
    collector.debug_html_dir = tmp_path / "html"

    with pytest.raises(CollectorBlockedError, match="CAPTCHA"):
        await collector.collect_search(
            Search(
                name="avito_samara_3rooms_secondary",
                source="avito",
                url=page.url,
                city="Самара",
                rooms=3,
                max_pages=1,
            ),
            _FakeContext(page),
        )

    assert page.closed is True
    assert collector.last_debug_html_path is not None


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
