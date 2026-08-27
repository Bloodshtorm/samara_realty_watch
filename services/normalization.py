from __future__ import annotations

import hashlib
import re
import unicodedata
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
    match = re.search(r"(\d+)\s*(?:-?\s*комн|комнат)", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def calc_price_per_m2(price_rub: int | None, area_total_m2: float | None) -> int | None:
    if not price_rub or not area_total_m2:
        return None
    return round(price_rub / area_total_m2)


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
        r"\bд\.?\s*": "дом ",
        r"\bкв\.?\s*\d+\b": "",
    }
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text)
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
    features = {name: None for name in FEATURE_NAMES}
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
