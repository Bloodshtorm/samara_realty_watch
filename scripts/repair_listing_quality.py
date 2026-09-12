"""Preview/repair derived listing data; preserve observations, prices and user states."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from scripts.infer_missing_coordinates import address_key
from services.geography import distance_km, usable_coordinates
from services.normalization import (
    detect_district,
    is_outside_samara_city_listing,
    mortgage_features,
)


def repair(path: Path, *, apply: bool = False) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode={'rw' if apply else 'ro'}", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if apply:
            backup_path = (
                path.parent
                / "backups"
                / ("before-quality-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + ".sqlite3")
            )
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(backup_path) as backup:
                conn.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup verification failed")
            print(f"Verified backup: {backup_path}")
        rows = conn.execute(
            "SELECT id,address_raw,address_normalized,district,title,description,features,"
            "latitude,longitude FROM listings"
        ).fetchall()
        positions: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for row in rows:
            if is_outside_samara_city_listing(row["address_raw"]):
                continue
            key = address_key(row["address_normalized"] or row["address_raw"])
            if key and usable_coordinates(row["latitude"], row["longitude"]):
                positions[key].append((row["latitude"], row["longitude"]))
        reliable = {
            key: points[0]
            for key, points in positions.items()
            if all(distance_km(*points[0], *point) <= 0.1 for point in points)
        }
        counts: Counter[str] = Counter()
        for row in rows:
            features = json.loads(row["features"] or "{}") or {}
            features.update(
                mortgage_features(" ".join(filter(None, [row["title"], row["description"]])))
            )
            district = detect_district(row["address_raw"])
            if district is None and row["district"] != "самарский":
                district = row["district"]
            lat, lng = row["latitude"], row["longitude"]
            if not usable_coordinates(lat, lng):
                if lat is not None or lng is not None:
                    counts["invalid_coordinates_cleared"] += 1
                lat = lng = None
                key = address_key(row["address_normalized"] or row["address_raw"])
                if key in reliable and not is_outside_samara_city_listing(row["address_raw"]):
                    lat, lng = reliable[key]
                    features["coordinates_inferred"] = True
                    counts["coordinates_inferred"] += 1
            if district != row["district"]:
                counts["districts_corrected"] += 1
            if features != json.loads(row["features"] or "{}"):
                counts["features_corrected"] += 1
            if apply:
                conn.execute(
                    "UPDATE listings SET district=?,features=?,latitude=?,longitude=? WHERE id=?",
                    (district, json.dumps(features, ensure_ascii=False), lat, lng, row["id"]),
                )
        if apply:
            conn.commit()
        return dict(counts)
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(repair(args.database.resolve(), apply=args.apply), ensure_ascii=False))
