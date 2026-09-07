from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models import Search
from collectors.base import CollectorBlockedError
from collectors.cian import CianCollector

OFFER = """<script>window._cianConfig = [{"cianId":123,"price":10000000,
"totalArea":"77.8","roomsCount":3,"floorNumber":18,
"building":{"floorsCount":19}}];</script>"""


def browser(pages):
    page = SimpleNamespace(url="", close=AsyncMock(), wait_for_timeout=AsyncMock())
    index = -1

    async def goto(url, **kwargs):
        nonlocal index
        index += 1
        page.url = pages[index].get("url", url)
        return SimpleNamespace(status=pages[index].get("status", 200))

    async def content():
        return pages[index]["html"]

    page.goto = AsyncMock(side_effect=goto)
    page.content = content
    return SimpleNamespace(new_page=AsyncMock(return_value=page)), page


async def test_cian_uses_shared_browser_and_stops_on_empty_later_page():
    context, page = browser([{"html": OFFER}, {"html": OFFER}, {"html": "Нет объявлений"}])
    collector = CianCollector()
    found = await collector.collect_search(
        Search(url="https://example.test/cat.php?room3=1", max_pages=4), context
    )
    assert len(found) == 1
    assert found[0].source_listing_id == "123"
    assert page.goto.await_count == 3
    assert page.goto.call_args_list[1].args[0].endswith("room3=1&p=2")
    page.close.assert_awaited_once()


@pytest.mark.parametrize(
    "response,exception",
    [
        ({"url": "https://example.test/cian-captcha/", "html": ""}, CollectorBlockedError),
        ({"status": 403, "html": ""}, CollectorBlockedError),
        ({"html": "<h1>Подтвердите, что вы человек</h1>"}, CollectorBlockedError),
        ({"html": "Пустая страница"}, RuntimeError),
        ({"status": 500, "html": "Ошибка сервера"}, RuntimeError),
    ],
)
async def test_cian_does_not_report_blocks_or_empty_first_page_as_success(response, exception):
    context, page = browser([response])
    collector = CianCollector()
    collector.save_debug_page = AsyncMock()
    with pytest.raises(exception):
        await collector.collect_search(Search(url="https://example.test/", max_pages=2), context)
    collector.save_debug_page.assert_awaited_once_with(page)
    page.close.assert_awaited_once()


async def test_cian_mid_search_captcha_is_not_partial_success():
    context, page = browser([{"html": OFFER}, {"html": "CAPTCHA"}])
    with pytest.raises(CollectorBlockedError):
        await CianCollector().collect_search(
            Search(url="https://example.test/", max_pages=2), context
        )
    page.close.assert_awaited_once()
