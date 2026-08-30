from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from playwright.async_api import BrowserContext

from app.models import Search
from app.schemas import ParsedListing
from collectors.debug import DebugMixin
from collectors.html_extract import (
    parsed_from_avito_cards,
    parsed_from_data_attrs,
    parsed_from_json_ld,
)


class AvitoCollector(DebugMixin):
    source_name = "avito"

    async def collect_search(self, search: Search, context: BrowserContext) -> list[ParsedListing]:
        page = await context.new_page()
        try:
            listings_by_id: dict[str, ParsedListing] = {}
            page_url = search.url
            for page_number in range(1, max(search.max_pages, 1) + 1):
                target_url = page_url if page_number == 1 else _page_url(page_url, page_number)
                await page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(1500)
                html = await page.content()
                text = (await page.locator("body").inner_text(timeout=10_000)).lower()
                if "#block" in page.url or "доступ ограничен" in text or "проблема с ip" in text:
                    raise RuntimeError("Avito access is blocked by IP/anti-bot page")

                parsed = (
                    parsed_from_avito_cards(
                        self.source_name,
                        html,
                        page.url,
                        property_type="land" if search.rooms == 0 else "flat",
                        require_rooms=bool(search.rooms),
                    )
                    or parsed_from_json_ld(self.source_name, html, page.url)
                    or parsed_from_data_attrs(self.source_name, html)
                )
                if search.rooms:
                    parsed = [listing for listing in parsed if listing.rooms == search.rooms]
                if not parsed:
                    break
                for listing in parsed:
                    listings_by_id[listing.source_listing_id] = listing
                page_url = page.url
            return list(listings_by_id.values())
        except Exception:
            await self.save_debug_page(page)
            raise
        finally:
            await page.close()


def _page_url(url: str, page_number: int) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["p"] = str(page_number)
    return urlunparse(parsed._replace(query=urlencode(query)))
