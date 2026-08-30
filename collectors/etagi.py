from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from playwright.async_api import BrowserContext

from app.models import Search
from app.schemas import ParsedListing
from collectors.html_extract import parsed_from_etagi_state, parsed_from_json_ld

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


class EtagiCollector:
    source_name = "etagi"

    async def collect_search(self, search: Search, context: BrowserContext) -> list[ParsedListing]:
        listings: dict[str, ParsedListing] = {}
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=60) as client:
            for page_num in range(1, search.max_pages + 1):
                url = search.url if page_num == 1 else _with_page(search.url, page_num)
                response = await client.get(url)
                response.raise_for_status()
                found = parsed_from_etagi_state(self.source_name, response.text, str(response.url))
                found = found or parsed_from_json_ld(
                    self.source_name, response.text, str(response.url)
                )
                if not found:
                    break
                before = len(listings)
                for listing in found:
                    listings[listing.source_listing_id] = listing
                if page_num > 1 and len(listings) == before:
                    break
        return list(listings.values())


def _with_page(url: str, page_num: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["page"] = str(page_num)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
