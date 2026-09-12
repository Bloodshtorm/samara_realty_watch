from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.async_api import BrowserContext

from app.models import Search
from app.schemas import ParsedListing
from collectors.base import CollectorBlockedError
from collectors.debug import DebugMixin
from collectors.html_extract import (
    page_looks_blocked,
    parsed_from_cian_state,
    parsed_from_data_attrs,
    parsed_from_json_ld,
)


class CianCollector(DebugMixin):
    source_name = "cian"

    async def collect_search(self, search: Search, context: BrowserContext) -> list[ParsedListing]:
        listings: dict[str, ParsedListing] = {}
        page = await context.new_page()
        try:
            for page_num in range(1, search.max_pages + 1):
                url = search.url if page_num == 1 else _with_page(search.url, page_num)
                response = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                self.pages_processed += 1
                await page.wait_for_timeout(1500)
                html = await page.content()
                if "cian-captcha" in page.url or (
                    response is not None and response.status in (401, 403, 429)
                ):
                    raise CollectorBlockedError("Cian requires manual authentication/CAPTCHA")
                if response is not None and response.status >= 400:
                    raise RuntimeError(f"Cian returned HTTP {response.status}")
                found = (
                    parsed_from_cian_state(self.source_name, html, page.url)
                    or parsed_from_json_ld(self.source_name, html, page.url)
                    or parsed_from_data_attrs(self.source_name, html)
                )
                if not found:
                    if page_looks_blocked(html):
                        raise CollectorBlockedError("Cian requires manual authentication/CAPTCHA")
                    if page_num == 1:
                        raise RuntimeError("Cian returned no parseable listings on the first page")
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
