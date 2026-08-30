from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.async_api import BrowserContext

from app.models import Search
from app.schemas import ParsedListing
from collectors.debug import DebugMixin
from collectors.html_extract import page_looks_blocked, parsed_from_mirkvartir_cards


class MirKvartirCollector(DebugMixin):
    source_name = "mirkvartir"

    async def collect_search(self, search: Search, context: BrowserContext) -> list[ParsedListing]:
        page = await context.new_page()
        listings: dict[str, ParsedListing] = {}
        try:
            for page_num in range(1, search.max_pages + 1):
                url = search.url if page_num == 1 else _with_page(search.url, page_num)
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(1500)
                html = await page.content()
                if page_looks_blocked(html):
                    await self.save_debug_page(page)
                    return list(listings.values())
                found = parsed_from_mirkvartir_cards(self.source_name, html, page.url)
                if not found:
                    break
                for listing in found:
                    listings[listing.source_listing_id] = listing
            return list(listings.values())
        except Exception:
            await self.save_debug_page(page)
            raise
        finally:
            await page.close()


def _with_page(url: str, page_num: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["p"] = str(page_num)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
