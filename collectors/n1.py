from __future__ import annotations

import httpx
from playwright.async_api import BrowserContext

from app.models import Search
from app.schemas import ParsedListing
from collectors.html_extract import parsed_from_json_ld, parsed_from_n1_state

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


class N1Collector:
    source_name = "n1"

    async def collect_search(self, search: Search, context: BrowserContext) -> list[ParsedListing]:
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=60) as client:
            response = await client.get(search.url)
            response.raise_for_status()
        found = parsed_from_n1_state(self.source_name, response.text, str(response.url))
        return found or parsed_from_json_ld(self.source_name, response.text, str(response.url))
