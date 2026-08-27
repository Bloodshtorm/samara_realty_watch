from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from app.schemas import ParsedListing
from services.normalization import (
    calc_price_per_m2,
    canonicalize_url,
    compact_text,
    detect_district,
    extract_features,
    normalize_address,
    normalize_seller_type,
    parse_area_m2,
    parse_floor,
    parse_price_rub,
    parse_rooms,
    stable_listing_id,
)


def extract_json_ld(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    for tag in soup.select('script[type="application/ld+json"]'):
        raw = tag.string or tag.get_text(strip=True)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            items.extend(x for x in parsed if isinstance(x, dict))
        elif isinstance(parsed, dict):
            graph = parsed.get("@graph")
            if isinstance(graph, list):
                items.extend(x for x in graph if isinstance(x, dict))
            items.append(parsed)
    return items


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def parsed_from_json_ld(source: str, html: str, page_url: str) -> list[ParsedListing]:
    listings: list[ParsedListing] = []
    for item in extract_json_ld(html):
        item_type = str(item.get("@type", "")).lower()
        if not any(t in item_type for t in ("apartment", "offer", "product", "residence")):
            continue
        url = _first(item.get("url")) or page_url
        canonical_url = canonicalize_url(str(url))
        title = compact_text(_first(item.get("name")) or item.get("headline"))
        description = compact_text(item.get("description"))
        offers = item.get("offers") if isinstance(item.get("offers"), dict) else {}
        price = parse_price_rub(offers.get("price") or item.get("price"))
        floor, floors_total = parse_floor(" ".join(x for x in (title, description) if x))
        area = parse_area_m2(
            item.get("floorSize")
            or item.get("size")
            or item.get("area")
            or " ".join(x for x in (title, description) if x)
        )
        address_raw = compact_text(
            item.get("address")
            if isinstance(item.get("address"), str)
            else str(item.get("address", ""))
        )
        normalized_address = normalize_address(address_raw)
        text = " ".join(x for x in (title, description, address_raw) if x)
        listings.append(
            ParsedListing(
                source=source,
                source_listing_id=stable_listing_id(source, canonical_url),
                url=str(url),
                canonical_url=canonical_url,
                title=title,
                address_raw=address_raw,
                address_normalized=normalized_address,
                district=detect_district(address_raw, description),
                seller_type=normalize_seller_type(text),
                rooms=parse_rooms(text),
                area_total_m2=area,
                price_rub=price,
                price_per_m2=calc_price_per_m2(price, area),
                floor=floor,
                floors_total=floors_total,
                description=description,
                raw_payload=item,
                features=extract_features(text),
            )
        )
    return listings


def parsed_from_data_attrs(source: str, html: str) -> list[ParsedListing]:
    soup = BeautifulSoup(html, "lxml")
    result: list[ParsedListing] = []
    for card in soup.select("[data-listing-id], [data-id][data-url]"):
        listing_id = card.get("data-listing-id") or card.get("data-id")
        url = card.get("data-url") or ""
        if not listing_id or not url:
            continue
        text = compact_text(card.get_text(" ", strip=True)) or ""
        price = parse_price_rub(card.get("data-price") or text)
        area = parse_area_m2(card.get("data-area") or text)
        floor, floors_total = parse_floor(text)
        canonical_url = canonicalize_url(url)
        result.append(
            ParsedListing(
                source=source,
                source_listing_id=str(listing_id),
                url=url,
                canonical_url=canonical_url,
                title=compact_text(card.get("data-title")) or text[:200],
                address_raw=compact_text(card.get("data-address")),
                address_normalized=normalize_address(card.get("data-address")),
                district=detect_district(card.get("data-address"), text),
                seller_type=normalize_seller_type(text),
                rooms=parse_rooms(text),
                area_total_m2=area,
                price_rub=price,
                price_per_m2=calc_price_per_m2(price, area),
                floor=floor,
                floors_total=floors_total,
                description=text,
                raw_payload=dict(card.attrs),
                features=extract_features(text),
            )
        )
    return result


def page_looks_blocked(html: str) -> bool:
    text = re.sub(r"\s+", " ", BeautifulSoup(html, "lxml").get_text(" ", strip=True)).lower()
    markers = ("captcha", "капча", "подтвердите", "войдите", "авторизуйтесь", "доступ ограничен")
    return any(marker in text for marker in markers)
