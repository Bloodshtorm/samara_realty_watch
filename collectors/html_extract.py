from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

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


def _attr_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return str(value[0]) if value else None
    return str(value)


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
        raw_offers = item.get("offers")
        offers: dict[str, Any] = raw_offers if isinstance(raw_offers, dict) else {}
        price = parse_price_rub(offers.get("price") or item.get("price"))
        floor, floors_total = parse_floor(" ".join(x for x in (title, description) if x))
        area = parse_area_m2(
            item.get("floorSize")
            or item.get("size")
            or item.get("area")
            or " ".join(x for x in (title, description) if x)
        )
        if "/offer/" not in canonical_url and price is None:
            continue
        if area is not None and area < 10:
            area = None
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


def parsed_from_offer_links(source: str, html: str, page_url: str) -> list[ParsedListing]:
    soup = BeautifulSoup(html, "lxml")
    by_id: dict[str, ParsedListing] = {}
    for card in soup.select('[data-test="OffersSerpItem"]'):
        link = card.select_one('a[href*="/offer/"]')
        if link is None:
            continue
        listing = _parsed_from_offer_link(source, link, page_url, card.get_text(" ", strip=True))
        if listing:
            by_id[listing.source_listing_id] = listing
    for link in soup.select('a[href*="/offer/"]'):
        listing = _parsed_from_offer_link(source, link, page_url, _nearest_listing_text(link))
        if listing is None:
            continue
        if listing.source_listing_id in by_id:
            existing = by_id[listing.source_listing_id]
            existing.area_total_m2 = existing.area_total_m2 or listing.area_total_m2
            existing.rooms = existing.rooms or listing.rooms
            existing.floor = existing.floor or listing.floor
            existing.floors_total = existing.floors_total or listing.floors_total
            existing.price_rub = existing.price_rub or listing.price_rub
            existing.price_per_m2 = existing.price_per_m2 or listing.price_per_m2
            existing.address_raw = existing.address_raw or listing.address_raw
            existing.address_normalized = (
                existing.address_normalized or listing.address_normalized
            )
            existing.district = existing.district or listing.district
            continue
        by_id[listing.source_listing_id] = listing
    return list(by_id.values())


def _parsed_from_offer_link(
    source: str,
    link,
    page_url: str,
    text: str,
) -> ParsedListing | None:
        href = link.get("href")
        if not href:
            return None
        url = urljoin(page_url, href)
        canonical_url = canonicalize_url(url)
        source_id = stable_listing_id(source, canonical_url)
        area = parse_area_m2(text)
        rooms = parse_rooms(text)
        floor, floors_total = parse_floor(text)
        price = _listing_price_from_text(text)
        price_per_m2 = _price_per_m2_from_text(text) or calc_price_per_m2(price, area)
        if not any((area, rooms, floor, floors_total)):
            return None
        address = _address_from_card_text(text)
        return ParsedListing(
            source=source,
            source_listing_id=source_id,
            url=url,
            canonical_url=canonical_url,
            title=_title_from_card_text(text),
            address_raw=address,
            address_normalized=normalize_address(address),
            district=detect_district(address, text),
            seller_type=normalize_seller_type(text),
            rooms=rooms,
            area_total_m2=area,
            price_rub=price,
            price_per_m2=price_per_m2,
            floor=floor,
            floors_total=floors_total,
            description=text,
            raw_payload={"href": href, "card_text": text},
            features=extract_features(text),
        )


def parsed_from_data_attrs(source: str, html: str) -> list[ParsedListing]:
    soup = BeautifulSoup(html, "lxml")
    result: list[ParsedListing] = []
    for card in soup.select("[data-listing-id], [data-id][data-url]"):
        listing_id = _attr_str(card.get("data-listing-id") or card.get("data-id"))
        url = _attr_str(card.get("data-url")) or ""
        if not listing_id or not url:
            continue
        text = compact_text(card.get_text(" ", strip=True)) or ""
        address = _attr_str(card.get("data-address"))
        price = parse_price_rub(_attr_str(card.get("data-price")) or text)
        area = parse_area_m2(_attr_str(card.get("data-area")) or text)
        floor, floors_total = parse_floor(text)
        canonical_url = canonicalize_url(url)
        result.append(
            ParsedListing(
                source=source,
                source_listing_id=listing_id,
                url=url,
                canonical_url=canonical_url,
                title=compact_text(_attr_str(card.get("data-title"))) or text[:200],
                address_raw=compact_text(address),
                address_normalized=normalize_address(address),
                district=detect_district(address, text),
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


def _nearest_listing_text(link) -> str:
    texts: list[str] = []
    current = link
    for _ in range(5):
        current = current.parent
        if current is None:
            break
        text = compact_text(current.get_text(" ", strip=True)) or ""
        if "/offer/" in str(current) and len(text) > 20:
            texts.append(text)
        if "комнат" in text and ("м²" in text or "м2" in text):
            return text
    return max(texts, key=len, default=compact_text(link.get_text(" ", strip=True)) or "")


def _title_from_card_text(text: str) -> str | None:
    match = re.search(r"\d+(?:[,.]\d+)?\s*м²\s*·\s*\d+[- ]?комнатная квартира", text)
    return match.group(0) if match else (text[:200] if text else None)


def _listing_price_from_text(text: str) -> int | None:
    prices = [
        parsed
        for value in re.findall(r"(?<!\d)(\d{1,3}(?:[\s\xa0]\d{3})+|\d{5,})(?=\s*₽)", text)
        if (parsed := parse_price_rub(value)) is not None
    ]
    return max(prices) if prices else None


def _price_per_m2_from_text(text: str) -> int | None:
    match = re.search(
        r"(?<!\d)(\d{1,3}(?:[\s\xa0]\d{3})+|\d{5,})(?=\s*₽\s*за\s*м²)",
        text,
    )
    return parse_price_rub(match.group(1)) if match else None


def _address_from_card_text(text: str) -> str | None:
    title = _title_from_card_text(text)
    if not title:
        floor_match = re.search(r"\d+\s*этаж\s*из\s*\d+", text, re.IGNORECASE)
        tail = text[floor_match.end() :] if floor_match else text
    else:
        tail = text.split(title, 1)[-1].strip()
    tail = re.sub(r"^·?\s*\d+\s*этаж\s*из\s*\d+\s*", "", tail, flags=re.IGNORECASE)
    price_match = re.search(r"\d[\d\s\xa0]{2,}\s*₽", tail)
    if price_match:
        tail = tail[: price_match.start()]
    parts = re.split(
        (
            r"\s{2,}| Прода[еёе]тся | Продам | Для вас | Срочная продажа | "
            r"Номер объекта:| Идентификатор объекта:| Добавочный номер | Арт\. | "
            r"3\s*[- ]?\s*х комнатная | ГОТОВАЯ | ЖК\b| Информация о квартире:| Назовите номер"
        ),
        tail,
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    address = compact_text(parts[0])
    if address:
        return address
    match = re.search(
        r"((?:улица|проспект|шоссе|переулок|Самара).{3,120}?)(?:Прода[еёе]тся|ГОТОВАЯ|ЖК|\\d[\\d\\s\\xa0]{2,}\\s*₽)",
        text,
        flags=re.IGNORECASE,
    )
    return compact_text(match.group(1)) if match else None


def page_looks_blocked(html: str) -> bool:
    text = re.sub(r"\s+", " ", BeautifulSoup(html, "lxml").get_text(" ", strip=True)).lower()
    markers = ("captcha", "капча", "подтвердите", "войдите", "авторизуйтесь", "доступ ограничен")
    return any(marker in text for marker in markers)
