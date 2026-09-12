"""Check process liveness, browser connectivity, and successful collection freshness."""

import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from urllib.request import urlopen

assert time.time() - os.stat("/tmp/scheduler-heartbeat").st_mtime < 120
with urlopen("http://127.0.0.1:9222/json/version", timeout=5) as response:
    assert response.status == 200
with sqlite3.connect("file:/app/data/realty.sqlite3?mode=ro", uri=True, timeout=5) as conn:
    row = conn.execute(
        "SELECT max(last_completed_at), min(interval_hours) FROM searches WHERE enabled=1"
    ).fetchone()
    if row[0]:
        latest = datetime.fromisoformat(row[0]).replace(tzinfo=UTC)
        assert datetime.now(UTC) - latest < timedelta(hours=(row[1] or 3) + 3)
