"""Offline SQLite compaction for the MVP. Stop web and collector services first."""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.retention import prune_history


def backup_database(path: Path, backup: Path) -> None:
    with tempfile.TemporaryDirectory(dir=backup.parent) as directory:
        snapshot = Path(directory) / "snapshot.sqlite3"
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
                if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise RuntimeError("Backup integrity check failed")
        with snapshot.open("rb") as source, gzip.open(backup, "xb", compresslevel=1) as target:
            shutil.copyfileobj(source, target)
        with snapshot.open("rb") as source, gzip.open(backup, "rb") as restored:
            if (
                hashlib.file_digest(source, "sha256").digest()
                != hashlib.file_digest(restored, "sha256").digest()
            ):
                raise RuntimeError("Compressed backup verification failed")


async def compact_database(path: Path) -> None:
    engine = create_async_engine(URL.create("sqlite+aiosqlite", database=str(path)))
    try:
        factory = async_sessionmaker(engine)
        async with factory() as session, session.begin():
            await prune_history(session, compact=True)
    finally:
        await engine.dispose()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("VACUUM")
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise RuntimeError("Compacted database integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Compacted database foreign key check failed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true", help="Back up and compact offline SQLite")
    args = parser.parse_args()
    path = args.database.resolve(strict=True)
    before = path.stat().st_size
    print(f"Database: {path}\nBytes before: {before}", flush=True)
    if not args.apply:
        print(
            "Dry run: would keep first/last observations per listing/search and remove snapshots."
        )
        return
    if shutil.disk_usage(path.parent).free < before * 3:
        raise RuntimeError(
            "Need free disk space of at least 3x database size for backup and VACUUM"
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.before-mvp-compact-{stamp}.gz")
    print(f"Backup: {backup}", flush=True)
    backup_database(path, backup)
    print(f"Verified compressed backup: {backup.stat().st_size} bytes", flush=True)
    asyncio.run(compact_database(path))
    print(f"Bytes after: {path.stat().st_size}", flush=True)


if __name__ == "__main__":
    main()
