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


def parsed_from_avito_cards(source: str, html: str, page_url: str) -> list[ParsedListing]:
    soup = BeautifulSoup(html, "lxml")
    by_id: dict[str, ParsedListing] = {}
    for card in soup.select('[data-marker="item"]'):
        title_link = card.select_one('[data-marker="item-title"][href]')
        if title_link is None:
            continue
        href = title_link.get("href")
        if not href:
            continue
        href = str(href)
        title = compact_text(title_link.get_text(" ", strip=True))
        rooms = parse_rooms(title or "")
        area = parse_area_m2(title or "")
        floor, floors_total = parse_floor(title or "")
        if area is None or rooms is None:
            continue

        url = urljoin(page_url, href)
        canonical_url = canonicalize_url(url)
        source_id = _avito_id_from_url(canonical_url) or stable_listing_id(source, canonical_url)
        text = compact_text(card.get_text(" ", strip=True)) or ""
        address = _avito_address(card, text)
        price = _listing_price_from_text(text)
        price_per_m2 = _price_per_m2_from_text(text) or calc_price_per_m2(price, area)
        description = _avito_description(text)

        by_id[source_id] = ParsedListing(
            source=source,
            source_listing_id=source_id,
            url=url,
            canonical_url=canonical_url,
            title=title,
            address_raw=address,
            address_normalized=normalize_address(address),
            district=detect_district(address, description),
            seller_type=normalize_seller_type(text),
            rooms=rooms,
            area_total_m2=area,
            price_rub=price,
            price_per_m2=price_per_m2,
            floor=floor,
            floors_total=floors_total,
            description=description,
            raw_payload={"href": href, "card_text": text},
            features=extract_features(text),
        )
    return list(by_id.values())


def parsed_from_mirkvartir_cards(source: str, html: str, page_url: str) -> list[ParsedListing]:
    soup = BeautifulSoup(html, "lxml")
    by_id: dict[str, ParsedListing] = {}
    for card in soup.find_all("article"):
        text = compact_text(card.get_text(" ", strip=True)) or ""
        id_match = re.search(r"(?:№|No)\s*([\d-]+)", text)
        if not id_match or not re.search(r"м[²2]", text, flags=re.IGNORECASE):
            continue

        source_id = id_match.group(1).replace("-", "")
        url = urljoin(page_url, f"/{source_id}/")
        canonical_url = canonicalize_url(url)
        title = _mirkvartir_title(text)
        address = _mirkvartir_address(card, text)
        price = _listing_price_from_text(text)
        area = parse_area_m2(title or text)
        floor, floors_total = parse_floor(title or text)
        price_per_m2 = _price_per_m2_from_text(text) or calc_price_per_m2(price, area)
        description = _mirkvartir_description(text)

        by_id[source_id] = ParsedListing(
            source=source,
            source_listing_id=source_id,
            url=url,
            canonical_url=canonical_url,
            title=title,
            address_raw=address,
            address_normalized=normalize_address(address),
            district=detect_district(address, description),
            seller_type=normalize_seller_type(text),
            rooms=parse_rooms(title or text),
            area_total_m2=area,
            area_kitchen_m2=_mirkvartir_kitchen_area(text),
            price_rub=price,
            price_per_m2=price_per_m2,
            floor=floor,
            floors_total=floors_total,
            description=description,
            raw_payload={"card_text": text},
            features=extract_features(text),
        )
    return list(by_id.values())


def _avito_id_from_url(url: str) -> str | None:
    match = re.search(r"_(\d+)(?:\?|$)", url)
    return match.group(1) if match else None


def _avito_address(card, text: str) -> str | None:
    values: list[str] = []
    for marker in ("street_link", "house_link"):
        node = card.select_one(f'[data-marker="{marker}"]')
        if node is not None:
            value = compact_text(node.get_text(" ", strip=True))
            if value:
                values.append(value)
    district_match = re.search(r"\bр-н\s+[А-Яа-яЁё-]+", text)
    if district_match:
        values.append(district_match.group(0))
    if values:
        return compact_text(", ".join(values).replace(" ,", ","))
    title = _title_from_card_text(text)
    tail = text.split(title, 1)[-1] if title and title in text else text
    price_match = re.search(r"\d[\d\s\xa0]{2,}\s*₽", tail)
    if price_match:
        tail = tail[price_match.end() :]
    return _address_from_card_text(tail)


def _avito_description(text: str) -> str | None:
    parts = re.split(
        r"(?:Показать телефон|Написать|Позвонить|На Авито с|Разместить объявление)",
        text,
        maxsplit=1,
    )
    return compact_text(parts[0])


def parsed_from_cian_state(source: str, html: str, page_url: str) -> list[ParsedListing]:
    by_id: dict[str, ParsedListing] = {}
    for payload in _json_objects_matching(html, r'"cianId"\s*:'):
        cian_id = payload.get("cianId") or payload.get("id")
        price = parse_price_rub(payload.get("price") or payload.get("formattedShortPrice"))
        area = parse_area_m2(payload.get("totalArea") or payload.get("formattedFullInfo"))
        if not cian_id or not price or not area:
            continue

        raw_url = payload.get("fullUrl") or payload.get("url")
        url = urljoin(page_url, str(raw_url or f"/sale/flat/{cian_id}/"))
        canonical_url = canonicalize_url(url)
        building = _dict_value(payload, "building")
        geo = _dict_value(payload, "geo")
        coordinates = _dict_value(geo, "coordinates")
        address = _cian_address(payload)
        title = compact_text(
            payload.get("title")
            or payload.get("formattedFullInfo")
            or f"{payload.get('roomsCount') or ''}-комн. квартира, {area:g} м²"
        )
        description = compact_text(payload.get("description"))
        text = " ".join(x for x in (title, description, address) if x)
        source_id = str(cian_id)
        by_id[source_id] = ParsedListing(
            source=source,
            source_listing_id=source_id,
            url=url,
            canonical_url=canonical_url,
            title=title,
            address_raw=address,
            address_normalized=normalize_address(address),
            district=detect_district(address, description),
            latitude=_optional_float_value(coordinates.get("lat") or geo.get("lat")),
            longitude=_optional_float_value(coordinates.get("lng") or geo.get("lng")),
            property_type=str(payload.get("category") or payload.get("offerType") or "flat"),
            seller_type=normalize_seller_type(text),
            rooms=_optional_int_value(payload.get("roomsCount")) or parse_rooms(text),
            area_total_m2=area,
            area_kitchen_m2=parse_area_m2(payload.get("kitchenArea")),
            price_rub=price,
            price_per_m2=calc_price_per_m2(price, area),
            floor=_optional_int_value(payload.get("floorNumber")),
            floors_total=_optional_int_value(building.get("floorsCount")),
            building_year=_optional_int_value(building.get("buildYear")),
            building_type=compact_text(building.get("materialType")),
            description=description,
            photos_count=_optional_int_value(payload.get("photosCount")),
            raw_payload=payload,
            features=extract_features(text),
        )
    return list(by_id.values())


def parsed_from_n1_state(source: str, html: str, page_url: str) -> list[ParsedListing]:
    by_id: dict[str, ParsedListing] = {}
    for payload in _json_objects_matching(html, r'"objectType"\s*:\s*"offer"'):
        offer_id = payload.get("id") or payload.get("_id") or payload.get("trackId")
        params = _dict_value(payload, "params")
        price = parse_price_rub(payload.get("price") or params.get("price"))
        area = _n1_area(params.get("total_area"))
        if not offer_id or not price or not area:
            continue

        raw_url = payload.get("url")
        url = urljoin(page_url, str(raw_url or f"/view/{offer_id}/"))
        canonical_url = canonicalize_url(url)
        address = _n1_address(payload, params)
        description = compact_text(params.get("description") or payload.get("description"))
        text = " ".join(x for x in (address, description) if x)
        title = compact_text(
            payload.get("title")
            or f"{params.get('rooms_count') or ''}-комн. квартира, {area:g} м²"
        )
        location = _dict_value(payload, "location")
        if not location:
            location = _dict_value(params, "location")
        source_id = str(offer_id)

        by_id[source_id] = ParsedListing(
            source=source,
            source_listing_id=source_id,
            url=url,
            canonical_url=canonical_url,
            title=title,
            address_raw=address,
            address_normalized=normalize_address(address),
            district=detect_district(address, description),
            latitude=_optional_float_value(location.get("lat")),
            longitude=_optional_float_value(location.get("lon")),
            property_type=str(payload.get("rubric") or "flat"),
            seller_type="agent" if payload.get("is_agency") else normalize_seller_type(text),
            rooms=_optional_int_value(params.get("rooms_count")),
            area_total_m2=area,
            area_living_m2=_n1_area(params.get("living_area")),
            area_kitchen_m2=_n1_area(params.get("kitchen_area")),
            price_rub=price,
            price_per_m2=calc_price_per_m2(price, area),
            floor=_optional_int_value(params.get("floor")),
            floors_total=_optional_int_value(params.get("floors_count")),
            description=description,
            photos_count=(
                _optional_int_value(payload.get("photos_count"))
                or (
                    len(payload.get("photos", []))
                    if isinstance(payload.get("photos"), list)
                    else None
                )
            ),
            raw_payload=payload,
            features=extract_features(text),
        )
    return list(by_id.values())


def parsed_from_etagi_state(source: str, html: str, page_url: str) -> list[ParsedListing]:
    by_id: dict[str, ParsedListing] = {}
    for payload in _json_objects_matching(html, r'"object_id"\s*:'):
        object_id = payload.get("object_id")
        price = parse_price_rub(payload.get("price"))
        area = _optional_float_value(payload.get("square"))
        if not object_id or not price or not area:
            continue

        source_id = str(object_id)
        url = urljoin(page_url, f"/realty/{source_id}/")
        canonical_url = canonicalize_url(url)
        meta = _dict_value(payload, "meta")
        address = _etagi_address(payload, meta)
        title = compact_text(
            f"{payload.get('rooms') or ''}-комн. квартира, {area:g} м²"
        )
        description = compact_text(payload.get("description"))
        text = " ".join(x for x in (title, address, description, str(meta.get("walls") or "")) if x)

        by_id[source_id] = ParsedListing(
            source=source,
            source_listing_id=source_id,
            url=url,
            canonical_url=canonical_url,
            title=title,
            address_raw=address,
            address_normalized=normalize_address(address),
            district=detect_district(address, description),
            latitude=_optional_float_value(payload.get("la")),
            longitude=_optional_float_value(payload.get("lo")),
            property_type=str(payload.get("type") or "flat"),
            seller_type="agent",
            rooms=_optional_int_value(payload.get("rooms")),
            area_total_m2=area,
            price_rub=price,
            price_per_m2=_optional_int_value(payload.get("price_m2"))
            or calc_price_per_m2(price, area),
            floor=_optional_int_value(payload.get("floor")),
            floors_total=_optional_int_value(payload.get("floors")),
            building_year=_optional_int_value(payload.get("building_year")),
            building_type=compact_text(meta.get("walls")),
            description=description,
            photos_count=_optional_int_value(_dict_value(payload, "media").get("photos")),
            raw_payload=payload,
            features=extract_features(text),
        )
    return list(by_id.values())


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
        r"(?<!\d)(\d{1,3}(?:[\s\xa0]\d{3})+|\d{5,})(?=\s*₽\s*(?:за\s*)?м[²2])",
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


def _mirkvartir_title(text: str) -> str | None:
    match = re.search(
        r"\d+\s*-\s*комн\.\s*квартира,\s*\d+(?:[,.]\d+)?\s*м[²2],\s*\d+\s*/\s*\d+\s*этаж",
        text,
        flags=re.IGNORECASE,
    )
    return compact_text(match.group(0)) if match else None


def _mirkvartir_address(card, text: str) -> str | None:
    address_node = card.select_one("p.kpMfk")
    if address_node is not None:
        return compact_text(address_node.get_text(" ", strip=True).replace(" ,", ","))
    match = re.search(r"(Самара\s*,\s*.+?)(?:\s+На карте|\s+[А-ЯЁ][а-яё]+ \w+|$)", text)
    return compact_text(match.group(1).replace(" ,", ",")) if match else None


def _mirkvartir_kitchen_area(text: str) -> float | None:
    match = re.search(r"(\d+(?:[,.]\d+)?)\s*м[²2]\s*кухня", text, flags=re.IGNORECASE)
    return parse_area_m2(match.group(1)) if match else None


def _mirkvartir_description(text: str) -> str | None:
    parts = re.split(r"\s+Позвонить\s+", text, maxsplit=1)
    if len(parts) == 2:
        return compact_text(re.split(r"\s+(?:№|No)\s+", parts[1], maxsplit=1)[0])
    return text


def _cian_address(payload: dict[str, Any]) -> str | None:
    geo = payload.get("geo")
    if not isinstance(geo, dict):
        return None
    address = geo.get("address")
    if isinstance(address, list):
        values = [item.get("title") for item in address if isinstance(item, dict)]
        return compact_text(", ".join(str(value) for value in values if value))
    if isinstance(address, str):
        return compact_text(address)
    return None


def _dict_value(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, dict) else {}


def _n1_address(payload: dict[str, Any], params: dict[str, Any]) -> str | None:
    links = payload.get("geo_links")
    if isinstance(links, dict):
        values: list[str] = []
        for key in ("city", "district", "street", "house_number"):
            item = links.get(key)
            if isinstance(item, dict) and item.get("title"):
                values.append(str(item["title"]))
        if values:
            return compact_text(", ".join(values))

    city = params.get("city")
    district = params.get("district")
    values = []
    for item in (city, district):
        if isinstance(item, dict) and item.get("name_ru"):
            values.append(str(item["name_ru"]))
    return compact_text(", ".join(values)) if values else None


def _etagi_address(payload: dict[str, Any], meta: dict[str, Any]) -> str | None:
    values = [
        meta.get("city"),
        meta.get("district"),
        meta.get("street"),
        payload.get("house_num") or payload.get("house_address_number"),
    ]
    return compact_text(", ".join(str(value) for value in values if value))


def _n1_area(value: Any) -> float | None:
    parsed = _optional_float_value(value)
    if parsed is None:
        return None
    return parsed / 100 if parsed > 1000 else parsed


def _json_objects_matching(text: str, pattern: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for marker_match in re.finditer(pattern, text):
        bounds = _json_object_bounds(text, marker_match.start())
        if bounds is None or bounds in seen:
            continue
        seen.add(bounds)
        raw = text[bounds[0] : bounds[1]]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            objects.append(parsed)
    return objects


def _json_object_bounds(text: str, position: int) -> tuple[int, int] | None:
    start = position
    while start >= 0 and text[start] != "{":
        start -= 1
    while start >= 0:
        end = _matching_json_object_end(text, start)
        if end is not None and position < end:
            return start, end
        start = text.rfind("{", 0, start)
    return None


def _matching_json_object_end(text: str, start: int) -> int | None:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _optional_int_value(value: Any) -> int | None:
    parsed = parse_price_rub(value)
    return parsed


def _optional_float_value(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def page_looks_blocked(html: str) -> bool:
    text = re.sub(r"\s+", " ", BeautifulSoup(html, "lxml").get_text(" ", strip=True)).lower()
    markers = ("captcha", "капча", "подтвердите", "войдите", "авторизуйтесь", "доступ ограничен")
    return any(marker in text for marker in markers)
