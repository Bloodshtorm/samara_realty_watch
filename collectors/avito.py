from __future__ import annotations

import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from playwright.async_api import BrowserContext
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.models import Search
from app.schemas import ParsedListing
from collectors.base import CollectorBlockedError
from collectors.debug import DebugMixin
from collectors.html_extract import (
    page_looks_blocked,
    parsed_from_avito_cards,
    parsed_from_avito_detail,
    parsed_from_data_attrs,
    parsed_from_json_ld,
)
from services.avito_policy import AvitoPaused, AvitoPolicy, AvitoTransientError, retry_after_seconds


class AvitoCollector(DebugMixin):
    source_name = "avito"
    policy: AvitoPolicy | None = None
    start_page: int = 1
    batch_pages: int = 10
    next_page: int = 1
    partial_reason: str | None = None
    reached_end: bool = False

    async def collect_search(self, search: Search, context: BrowserContext) -> list[ParsedListing]:
        page = await context.new_page()
        self.partial_reason = None
        self.reached_end = False
        self.next_page = self.start_page
        try:
            listings_by_id: dict[str, ParsedListing] = {}
            page_url = search.url
            stop = max(search.max_pages, 1) + 1
            if self.policy:
                stop = min(stop, self.start_page + self.batch_pages)
            for page_number in range(self.start_page, stop):
                target_url = page_url if page_number == 1 else _page_url(page_url, page_number)
                if self.policy:
                    try:
                        await self.policy.before_page()
                    except AvitoPaused as exc:
                        if not listings_by_id:
                            raise
                        self.partial_reason = str(exc)
                        break
                try:
                    response = await page.goto(
                        target_url, wait_until="domcontentloaded", timeout=60_000
                    )
                except PlaywrightTimeoutError:
                    if self.policy:
                        self.policy.cooldown(3600, "Navigation timeout")
                    raise
                self.pages_processed += 1
                if response and response.status == 429:
                    if self.policy:
                        self.policy.cooldown(
                            max(
                                3600,
                                retry_after_seconds(
                                    response.headers.get("retry-after"), time.time()
                                ),
                            ),
                            "HTTP 429",
                        )
                    raise AvitoTransientError("Avito HTTP 429: source cooldown")
                if response and response.status in (401, 403):
                    raise CollectorBlockedError(
                        f"Avito HTTP {response.status}: manual verification required"
                    )
                if response and response.status >= 500:
                    if self.policy:
                        self.policy.cooldown(3600, f"HTTP {response.status}")
                    raise AvitoTransientError(f"Avito HTTP {response.status}: source cooldown")
                await _wait_for_avito_content(page)
                html = await page.content()
                text = (await page.locator("body").inner_text(timeout=10_000)).lower()
                if "#block" in page.url or _looks_like_avito_block(text, html):
                    raise CollectorBlockedError("Avito returned CAPTCHA/login/blocked page")

                parsed = (
                    parsed_from_avito_cards(
                        self.source_name,
                        html,
                        page.url,
                        property_type="land" if search.rooms == 0 else "flat",
                        require_rooms=bool(search.rooms),
                    )
                    or parsed_from_avito_detail(
                        self.source_name,
                        html,
                        page.url,
                        property_type="land" if search.rooms == 0 else "flat",
                    )
                    or parsed_from_json_ld(self.source_name, html, page.url)
                    or parsed_from_data_attrs(self.source_name, html)
                )
                if not parsed:
                    if page_number == 1:
                        raise RuntimeError(
                            "Avito returned no parseable listings on the first page; "
                            "possible CAPTCHA, changed markup, or empty search result"
                        )
                    self.next_page = 1
                    self.reached_end = True
                    break
                if search.rooms:
                    parsed = [listing for listing in parsed if listing.rooms == search.rooms]
                for listing in parsed:
                    listings_by_id[listing.source_listing_id] = listing
                page_url = page.url
                self.next_page = page_number + 1
            if self.next_page > search.max_pages:
                self.next_page = 1
            elif self.policy and self.next_page > 1 and not self.partial_reason:
                self.partial_reason = "Bounded batch; remaining pages deferred"
            return list(listings_by_id.values())
        except CollectorBlockedError as exc:
            if self.policy:
                self.policy.block(str(exc))
            await self.save_debug_page(page)
            raise
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


def _looks_like_avito_block(text: str, html: str) -> bool:
    markers = (
        "доступ ограничен",
        "проблема с ip",
        "captcha",
        "капча",
        "подтвердите",
        "проверка безопасности",
        "подозрительный трафик",
    )
    return any(marker in text for marker in markers) or page_looks_blocked(html)


async def _wait_for_avito_content(page) -> None:
    try:
        await page.wait_for_selector(
            '[data-marker="item"], [data-marker="item-title"], #captcha, #block',
            timeout=15_000,
        )
    except PlaywrightTimeoutError:
        await page.wait_for_timeout(1500)
