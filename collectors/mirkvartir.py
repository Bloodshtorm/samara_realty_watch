from __future__ import annotations

from playwright.async_api import BrowserContext

from app.models import Search
from app.schemas import ParsedListing
from collectors.debug import DebugMixin
from collectors.html_extract import page_looks_blocked, parsed_from_mirkvartir_cards


class MirKvartirCollector(DebugMixin):
    source_name = "mirkvartir"

    async def collect_search(self, search: Search, context: BrowserContext) -> list[ParsedListing]:
        page = await context.new_page()
        try:
            await page.goto(search.url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(3000)
            html = await page.content()
            if page_looks_blocked(html):
                await self.save_debug_page(page)
                return []
            return parsed_from_mirkvartir_cards(self.source_name, html, page.url)
        except Exception:
            await self.save_debug_page(page)
            raise
        finally:
            await page.close()
