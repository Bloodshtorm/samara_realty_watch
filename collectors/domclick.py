from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from playwright.async_api import BrowserContext

from app.models import Search
from app.schemas import ParsedListing
from collectors.debug import DebugMixin
from collectors.html_extract import parsed_from_data_attrs, parsed_from_json_ld
from services.normalization import (
    calc_price_per_m2,
    canonicalize_url,
    compact_text,
    detect_district,
    extract_features,
    normalize_address,
)


class DomclickCollector(DebugMixin):
    source_name = "domclick"

    async def collect_search(self, search: Search, context: BrowserContext) -> list[ParsedListing]:
        api_listings = await self._collect_api(search, context)
        if api_listings:
            return api_listings

        page = await context.new_page()
        try:
            await page.goto(search.url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(3000)
            html = await page.content()
            text = (await page.locator("body").inner_text(timeout=10_000)).lower()
            title = (await page.title()).lower()
            if "403 | домклик" in title or "запрос выглядит необычно" in text:
                raise RuntimeError("Domclick access is blocked by anti-bot page")
            # TODO: add exact selectors after saving real HTML to tests/fixtures/domclick.html.
            return parsed_from_json_ld(self.source_name, html, page.url) or parsed_from_data_attrs(
                self.source_name, html
            )
        except Exception:
            await self.save_debug_page(page)
            raise
        finally:
            await page.close()

    async def _collect_api(
        self, search: Search, context: BrowserContext
    ) -> list[ParsedListing]:
        listings: dict[str, ParsedListing] = {}
        limit = 20
        for page_number in range(max(search.max_pages, 1)):
            url = _api_url(search.url, offset=page_number * limit, limit=limit)
            response = await context.request.get(
                url,
                headers={
                    "Accept": "application/json,text/plain,*/*",
                    "Referer": "https://samara.domclick.ru/search",
                },
                timeout=60_000,
            )
            if not response.ok:
                if page_number == 0:
                    return []
                break
            payload = await response.json()
            items = payload.get("result", {}).get("items", [])
            if not isinstance(items, list):
                break
            for item in items:
                if isinstance(item, dict):
                    parsed = _parsed_from_api_item(self.source_name, item)
                    listings[parsed.source_listing_id] = parsed
            if len(items) < limit:
                break
        return list(listings.values())


def _api_url(search_url: str, *, offset: int, limit: int) -> str:
    parsed = urlparse(search_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("sort", "qi")
    query.setdefault("sort_dir", "desc")
    query.setdefault("experiment", "agent_deals_ranking")
    query["offset"] = str(offset)
    query["limit"] = str(limit)
    api_query = urlencode(query)
    return urlunparse(
        ("https", "bff-search-web.domclick.ru", "/api/offers/v1", "", api_query, "")
    )


def _parsed_from_api_item(source: str, item: dict[str, Any]) -> ParsedListing:
    object_info = _dict_value(item, "objectInfo")
    house = _dict_value(item, "house")
    location = _dict_value(item, "location")
    address = compact_text(_dict_value(item, "address").get("displayName"))
    description = compact_text(item.get("description"))
    area = _optional_float(object_info.get("area"))
    price = _optional_int(item.get("price"))
    rooms = _optional_int(object_info.get("rooms"))
    title = compact_text(
        f"{rooms or ''}-комн. квартира, {area:g} м²" if area else item.get("title")
    )
    text = " ".join(value for value in (title, address, description) if value)
    url = str(item.get("path") or f"https://samara.domclick.ru/card/sale__flat__{item['id']}")
    canonical_url = canonicalize_url(url)

    return ParsedListing(
        source=source,
        source_listing_id=str(item["id"]),
        url=url,
        canonical_url=canonical_url,
        title=title,
        address_raw=address,
        address_normalized=normalize_address(address),
        district=detect_district(address, description),
        latitude=_optional_float(location.get("lat")),
        longitude=_optional_float(location.get("lon")),
        property_type=str(item.get("offerType") or "flat"),
        seller_type="agent" if item.get("seller") else "unknown",
        rooms=rooms,
        area_total_m2=area,
        price_rub=price,
        price_per_m2=_optional_int(item.get("squarePrice")) or calc_price_per_m2(price, area),
        floor=_optional_int(object_info.get("floor")),
        floors_total=_optional_int(house.get("floors")),
        building_year=_optional_int(house.get("buildYear")),
        description=description,
        photos_count=len(item.get("photos", [])) if isinstance(item.get("photos"), list) else None,
        raw_payload=item,
        features=extract_features(text),
    )


def _dict_value(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
