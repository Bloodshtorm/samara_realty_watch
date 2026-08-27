from __future__ import annotations

from playwright.async_api import BrowserContext

from app.models import Search
from app.schemas import ParsedListing
from collectors.debug import DebugMixin
from collectors.html_extract import parsed_from_data_attrs, parsed_from_json_ld


class DomclickCollector(DebugMixin):
    source_name = "domclick"

    async def collect_search(self, search: Search, context: BrowserContext) -> list[ParsedListing]:
        page = await context.new_page()
        try:
            await page.goto(search.url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(3000)
            html = await page.content()
            # TODO: add exact selectors after saving real HTML to tests/fixtures/domclick.html.
            return parsed_from_json_ld(self.source_name, html, page.url) or parsed_from_data_attrs(
                self.source_name, html
            )
        except Exception:
            await self.save_debug_page(page)
            raise
        finally:
            await page.close()
