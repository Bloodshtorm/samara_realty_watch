from __future__ import annotations

import argparse
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import listings from an old static Samara Realty Watch HTML report."
    )
    parser.add_argument("report", type=Path)
    parser.add_argument("--db", type=Path, default=Path("data/realty.sqlite3"))
    args = parser.parse_args()

    html = args.report.read_text(encoding="utf-8")
    rows = parse_rows(html)
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON")
    search_id = ensure_search(conn)
    imported = skipped = observations = 0
    for row in rows:
        listing_id = existing_listing_id(conn, row["source"], row["source_listing_id"])
        if listing_id is None:
            listing_id = str(uuid.uuid4())
            insert_listing(conn, listing_id, row)
            imported += 1
        else:
            skipped += 1
        if not observation_exists(conn, listing_id, row["last_seen_at"]):
            insert_observation(conn, listing_id, search_id, row)
            observations += 1
    conn.commit()
    print(
        f"rows={len(rows)} imported={imported} "
        f"existing={skipped} observations_added={observations}"
    )


def parse_rows(html: str) -> list[dict[str, object]]:
    soup = BeautifulSoup(html, "lxml")
    result: list[dict[str, object]] = []
    for tr in soup.select("tbody tr"):
        cells = tr.find_all("td")
        if len(cells) < 10:
            continue
        link = cells[9].find("a")
        url = link.get("href") if link else ""
        if not url:
            continue
        source = clean(cells[6].get_text(" ", strip=True))
        result.append(
            {
                "source": source,
                "source_listing_id": source_listing_id(source, url),
                "url": url,
                "canonical_url": canonicalize_url(url),
                "price_rub": parse_int(cells[0].get_text()),
                "price_per_m2": parse_int(cells[1].get_text()),
                "area_total_m2": parse_float(cells[2].get_text()),
                "floor": parse_floor(cells[3].get_text())[0],
                "floors_total": parse_floor(cells[3].get_text())[1],
                "address_normalized": none_if_dash(clean(cells[4].get_text(" ", strip=True))),
                "district": none_if_dash(clean(cells[5].get_text(" ", strip=True))),
                "first_seen_at": parse_dt(cells[7].get_text()),
                "last_seen_at": parse_dt(cells[8].get_text()),
            }
        )
    return result


def ensure_search(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT id FROM searches WHERE name = ?",
        ("imported_old_report",),
    ).fetchone()
    if row:
        return str(row[0])
    search_id = str(uuid.uuid4())
    now = utc_now()
    conn.execute(
        """
        INSERT INTO searches (
            id, name, source, url, city, rooms, enabled, interval_hours, max_pages,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            search_id,
            "imported_old_report",
            "yandex_realty",
            "file://old-report",
            "Самара",
            3,
            0,
            24,
            1,
            now,
            now,
        ),
    )
    return search_id


def existing_listing_id(
    conn: sqlite3.Connection,
    source: object,
    source_listing_id: object,
) -> str | None:
    row = conn.execute(
        "SELECT id FROM listings WHERE source = ? AND source_listing_id = ?",
        (source, source_listing_id),
    ).fetchone()
    return str(row[0]) if row else None


def insert_listing(conn: sqlite3.Connection, listing_id: str, row: dict[str, object]) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO listings (
            id, source, source_listing_id, url, canonical_url, first_seen_at, last_seen_at,
            last_active_at, address_normalized, district, property_type, seller_type,
            rooms, area_total_m2, price_rub, price_per_m2, floor, floors_total,
            raw_payload, features, is_active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            listing_id,
            row["source"],
            row["source_listing_id"],
            row["url"],
            row["canonical_url"],
            row["first_seen_at"],
            row["last_seen_at"],
            row["last_seen_at"],
            row["address_normalized"],
            row["district"],
            "flat",
            "unknown",
            3,
            row["area_total_m2"],
            row["price_rub"],
            row["price_per_m2"],
            row["floor"],
            row["floors_total"],
            "{}",
            "{}",
            1,
            now,
            now,
        ),
    )


def observation_exists(conn: sqlite3.Connection, listing_id: str, observed_at: object) -> bool:
    row = conn.execute(
        "SELECT 1 FROM listing_observations WHERE listing_id = ? AND observed_at = ? LIMIT 1",
        (listing_id, observed_at),
    ).fetchone()
    return row is not None


def insert_observation(
    conn: sqlite3.Connection,
    listing_id: str,
    search_id: str,
    row: dict[str, object],
) -> None:
    conn.execute(
        """
        INSERT INTO listing_observations (
            id, listing_id, search_id, observed_at, price_rub, price_per_m2,
            is_active, title_snapshot, raw_payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            listing_id,
            search_id,
            row["last_seen_at"],
            row["price_rub"],
            row["price_per_m2"],
            1,
            row["address_normalized"],
            "{}",
        ),
    )


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip().lower()


def none_if_dash(value: str) -> str | None:
    return None if value in {"", "-"} else value


def parse_int(value: str) -> int | None:
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else None


def parse_float(value: str) -> float | None:
    match = re.search(r"\d+(?:[,.]\d+)?", value)
    return float(match.group(0).replace(",", ".")) if match else None


def parse_floor(value: str) -> tuple[int | None, int | None]:
    match = re.search(r"(\d+)\s*/\s*(\d+)", value)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None


def parse_dt(value: str) -> str:
    parsed = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")
    return parsed.replace(tzinfo=UTC).isoformat(sep=" ")


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc.lower()}{parts.path.rstrip('/')}"


def source_listing_id(source: str, url: str) -> str:
    match = re.search(r"(\d{5,})", canonicalize_url(url))
    if match:
        return match.group(1)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source}:{url}"))


def utc_now() -> str:
    return datetime.now(UTC).isoformat(sep=" ")


if __name__ == "__main__":
    main()
