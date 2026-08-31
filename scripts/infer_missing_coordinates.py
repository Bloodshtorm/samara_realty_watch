from __future__ import annotations

import argparse
import os
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from statistics import median
from urllib.parse import urlparse

from dotenv import load_dotenv

SAMARA_LATITUDE_RANGE = (52.95, 53.45)
SAMARA_LONGITUDE_RANGE = (49.75, 50.55)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Infer missing listing coordinates from already mapped local listings."
    )
    parser.add_argument("--source", default="avito")
    parser.add_argument("--price-max", type=int)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    conn = sqlite3.connect(_sqlite_path(os.environ.get("DATABASE_URL", "")))
    conn.row_factory = sqlite3.Row

    coordinate_index = _coordinate_index(conn, excluded_source=args.source)
    targets = _target_rows(conn, source=args.source, price_max=args.price_max, limit=args.limit)
    updated = 0
    matched = 0
    for row in targets:
        key = address_key(row["address_normalized"]) or address_key(row["address_raw"])
        if key is None or key not in coordinate_index:
            continue
        matched += 1
        latitude, longitude = coordinate_index[key]
        if args.dry_run:
            print(f"{row['id']} {key} -> {latitude:.6f}, {longitude:.6f}")
            continue
        conn.execute(
            "update listings set latitude = ?, longitude = ? where id = ?",
            (latitude, longitude, row["id"]),
        )
        updated += 1
    if not args.dry_run:
        conn.commit()
    print(f"Done. Targets {len(targets)}; matched {matched}; updated {updated}")


def _sqlite_path(database_url: str) -> Path:
    parsed = urlparse(database_url)
    if parsed.scheme != "sqlite+aiosqlite":
        raise SystemExit("Only sqlite+aiosqlite DATABASE_URL is supported by this script")
    return Path(parsed.path)


def _coordinate_index(
    conn: sqlite3.Connection,
    *,
    excluded_source: str,
) -> dict[str, tuple[float, float]]:
    grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in conn.execute(
        """
        select source, address_raw, address_normalized, latitude, longitude
        from listings
        where source != ?
          and latitude is not null
          and longitude is not null
        """,
        (excluded_source,),
    ):
        if not _inside_samara(row["latitude"], row["longitude"]):
            continue
        key = address_key(row["address_normalized"]) or address_key(row["address_raw"])
        if key is None:
            continue
        grouped[key].append((float(row["latitude"]), float(row["longitude"])))
    return {
        key: (
            float(median(latitude for latitude, _ in values)),
            float(median(longitude for _, longitude in values)),
        )
        for key, values in grouped.items()
    }


def _target_rows(
    conn: sqlite3.Connection,
    *,
    source: str,
    price_max: int | None,
    limit: int,
) -> list[sqlite3.Row]:
    conditions = [
        "source = ?",
        "is_active = 1",
        "latitude is null",
        "longitude is null",
        "(address_raw is not null or address_normalized is not null)",
    ]
    params: list[object] = [source]
    if price_max is not None:
        conditions.append("price_rub <= ?")
        params.append(price_max)
    params.append(limit)
    return list(
        conn.execute(
            f"""
            select id, address_raw, address_normalized, price_rub
            from listings
            where {' and '.join(conditions)}
            order by last_seen_at desc, price_rub asc
            limit ?
            """,
            params,
        )
    )


def address_key(address: str | None) -> str | None:
    if not address:
        return None
    text = address.lower().replace("ё", "е")
    text = re.sub(r"\bг\.\s*", "", text)
    text = re.sub(r"\bгород\s+", "", text)
    text = re.sub(r"\b(?:р-н|район)\b[^,]*", "", text)
    text = re.sub(r"\b[а-я-]+\s+район\b", "", text)
    parts = [part.strip() for part in text.split(",") if part.strip()]
    for index, part in enumerate(parts):
        match = re.search(r"\b(?:д(?:ом)?\.?\s*)?(\d+[а-яa-z]?)\b", part)
        if match is None:
            continue
        house = match.group(1).replace(" ", "")
        street = part[: match.start()].strip(" .-") or (parts[index - 1] if index else "")
        street_key = _street_key(street)
        if street_key is None:
            continue
        return f"{street_key}|{house}"
    return None


def _street_key(street: str) -> str | None:
    street = re.sub(r"\b(?:ул|улица)\b\.?", "", street)
    street = re.sub(r"\b(?:пр-т|проспект|просп)\b\.?", "", street)
    street = re.sub(r"\b(?:ш|шоссе)\b\.?", "", street)
    street = re.sub(r"\b(?:пер|переулок)\b\.?", "", street)
    street = re.sub(r"[^а-яa-z0-9]+", " ", street)
    words = [
        word
        for word in street.split()
        if word not in {"самара", "дом", "д", "м", "н", "микрорайон"}
    ]
    result = " ".join(words)
    return result if len(result) >= 3 else None


def _inside_samara(latitude: float, longitude: float) -> bool:
    return (
        SAMARA_LATITUDE_RANGE[0] <= latitude <= SAMARA_LATITUDE_RANGE[1]
        and SAMARA_LONGITUDE_RANGE[0] <= longitude <= SAMARA_LONGITUDE_RANGE[1]
    )


if __name__ == "__main__":
    main()
