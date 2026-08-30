from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.schemas import FEATURE_NAMES

SAMARA_DISTRICTS = {
    "железнодорожный": ("железнодорожн",),
    "кировский": ("кировск",),
    "красноглинский": ("красноглинск",),
    "куйбышевский": ("куйбышевск",),
    "ленинский": ("ленинск",),
    "октябрьский": ("октябрьск",),
    "промышленный": ("промышленн",),
    "самарский": ("самарск",),
    "советский": ("советск",),
}


def compact_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("\xa0", " ").replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", normalized).strip()


def parse_price_rub(value: str | int | float | None) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value)
    digits = re.sub(r"[^\d]", "", compact_text(str(value)) or "")
    return int(digits) if digits else None


def parse_area_m2(value: str | int | float | None) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return float(value)
    text = compact_text(str(value)) or ""
    match = re.search(r"(\d+(?:[,.]\d+)?)\s*(?:м2|м²|кв\.?\s*м)", text, re.IGNORECASE)
    if not match:
        match = re.search(r"(\d+(?:[,.]\d+)?)", text)
    return float(match.group(1).replace(",", ".")) if match else None


def parse_floor(value: str | None) -> tuple[int | None, int | None]:
    if not value:
        return None, None
    text = compact_text(value) or ""
    match = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.search(r"(\d+)\s*этаж\s*из\s*(\d+)", text, re.IGNORECASE)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None


def parse_rooms(value: str | int | None) -> int | None:
    if isinstance(value, int):
        return value
    if not value:
        return None
    text = compact_text(str(value)) or ""
    if re.search(r"\bстуди[яиюе]\b", text, re.IGNORECASE):
        return 0
    match = re.search(r"(\d+)\s*(?:-?\s*(?:комн|к\.)|комнат)", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def calc_price_per_m2(price_rub: int | None, area_total_m2: float | None) -> int | None:
    if not price_rub or not area_total_m2:
        return None
    return round(price_rub / area_total_m2)


def is_fractional_share_listing(*texts: str | None) -> bool:
    normalized = " ".join(_normalized_parts(texts))
    if not normalized:
        return False
    if re.search(r"\bне\s+дол[яиюе]\b", normalized):
        return False
    patterns = (
        r"\bдол[яиюе]\b",
        r"\b\d+\s*/\s*\d+\s+(?:квартир[аыеу]?|комнат[ауы]?)\b",
        r"\b(?:прода(?:м|ется)|продается|купить)\s+дол",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def _normalized_parts(texts: Iterable[str | None]) -> list[str]:
    return [text.lower() for text in (compact_text(value) for value in texts) if text]


def is_outside_samara_city_listing(*texts: str | None) -> bool:
    normalized = " ".join(_normalized_parts(texts))
    if not normalized:
        return False
    blocked_markers = (
        "новокуйбышевск",
        "петра дубрава",
        "стройкерамика",
        "придорожный",
        "кошелев-парк",
        "поселок",
        "посёлок",
        "пгт",
        "село",
        "деревня",
    )
    return any(marker in normalized for marker in blocked_markers)


def is_low_rise_building(floors_total: int | None) -> bool:
    return floors_total is not None and floors_total <= 5


def is_wrong_room_count(
    rooms: int | None,
    expected_rooms: int,
    *texts: str | None,
) -> bool:
    if rooms is not None:
        return rooms != expected_rooms
    text_rooms = parse_rooms(" ".join(_normalized_parts(texts)))
    return text_rooms != expected_rooms


def should_exclude_listing(
    *,
    title: str | None,
    description: str | None,
    property_type: str | None,
    rooms: int | None,
    address_raw: str | None,
    address_normalized: str | None,
    floors_total: int | None,
    expected_rooms: int | None = None,
) -> bool:
    return (
        is_fractional_share_listing(title, description, property_type)
        or is_outside_samara_city_listing(address_raw, address_normalized, title, description)
        or is_low_rise_building(floors_total)
        or (
            expected_rooms is not None
            and is_wrong_room_count(rooms, expected_rooms, title, description)
        )
    )


def normalize_address(value: str | None) -> str | None:
    text = compact_text(value)
    if not text:
        return None
    text = text.lower()
    replacements = {
        r"\bг\.?\s+": "",
        r"\bгород\s+": "",
        r"\bул\.?\s+": "улица ",
        r"\bпр-кт\b": "проспект",
        r"\bпросп\.?\s+": "проспект ",
        r"\bд\.?\s*(?=\d)": "дом ",
        r"\bкв\.?\s*\d+\b": "",
    }
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*$", "", text)
    return compact_text(text)


def detect_district(*texts: str | None) -> str | None:
    combined = " ".join(t for t in (compact_text(x) for x in texts) if t).lower()
    for district, markers in SAMARA_DISTRICTS.items():
        if any(marker in combined for marker in markers):
            return district
    return None


def normalize_seller_type(text: str | None) -> str:
    normalized = (compact_text(text) or "").lower()
    if any(x in normalized for x in ("собственник", "от собственника", "owner")):
        return "owner"
    if any(x in normalized for x in ("застройщик", "developer")):
        return "developer"
    if any(x in normalized for x in ("агент", "риелтор", "agency", "агентство")):
        return "agent"
    return "unknown"


def extract_features(text: str | None) -> dict[str, bool | None]:
    normalized = (compact_text(text) or "").lower()
    features: dict[str, bool | None] = {name: None for name in FEATURE_NAMES}
    patterns = {
        "mortgage_available": ("ипотек",),
        "family_mortgage": ("семейн",),
        "it_mortgage": ("it-ипот", "айти ипот", "it ипот"),
        "subsidized_mortgage": ("субсидирован",),
        "installment_available": ("рассроч",),
        "new_building": ("новострой", "застройщик", "жк "),
        "secondary_market": ("вторич",),
        "renovation_required": ("требует ремонт", "без ремонта"),
        "renovated": ("евроремонт", "с ремонтом", "ремонт"),
        "balcony": ("балкон",),
        "loggia": ("лоджи",),
        "elevator": ("лифт",),
        "parking": ("парков", "паркинг"),
        "owner_sale": ("собственник", "от собственника"),
        "agency_sale": ("агент", "риелтор", "агентство"),
        "bargain_possible": ("торг",),
        "alternative_deal": ("альтернатив",),
        "encumbrance_mentioned": ("обременен", "обременение"),
        "power_of_attorney_mentioned": ("доверенн",),
        "redevelopment_mentioned": ("перепланиров",),
    }
    for name, markers in patterns.items():
        if any(marker in normalized for marker in markers):
            features[name] = True
    return features


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_"))
    )
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), query, ""))


def stable_listing_id(source: str, canonical_url: str) -> str:
    id_match = re.search(r"(\d{5,})", canonical_url)
    if id_match:
        return id_match.group(1)
    digest = hashlib.sha256(f"{source}:{canonical_url}".encode()).hexdigest()
    return digest[:32]
