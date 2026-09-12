"""Persistent load limits. This does not bypass source access restrictions."""

from __future__ import annotations

import asyncio
import json
import random
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any


class AvitoPaused(RuntimeError):
    pass


class AvitoTransientError(AvitoPaused):
    pass


def retry_after_seconds(value: str | None, now: float) -> float:
    try:
        return max(0, float(value or ""))
    except ValueError:
        try:
            return max(0, parsedate_to_datetime(value or "").timestamp() - now)
        except (ValueError, TypeError, OverflowError):
            return 3600


class AvitoPolicy:
    def __init__(self, path: Path, *, daily_pages: int = 60, page_delay: int = 60):
        self.path = path
        self.daily_pages = daily_pages
        self.page_delay = page_delay

    @contextmanager
    def edit(self) -> Iterator[dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=10) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY, data TEXT)")
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT data FROM policy WHERE id=1").fetchone()
            state = json.loads(row[0]) if row else {}
            yield state
            conn.execute("INSERT OR REPLACE INTO policy VALUES (1, ?)", (json.dumps(state),))

    def snapshot(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        with sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT data FROM policy WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {}

    def reason(self, state: dict[str, Any], now: float) -> str | None:
        if state.get("blocked"):
            return "Avito paused: manual browser verification required"
        if state.get("cooldown_until", 0) > now:
            return "Avito cooldown until " + time.strftime(
                "%Y-%m-%d %H:%M UTC", time.gmtime(state["cooldown_until"])
            )
        if state.get("window_until", 0) > now and state.get("pages", 0) >= self.daily_pages:
            return "Avito rolling 24-hour page budget exhausted"
        return None

    def check(self) -> None:
        reason = self.reason(self.snapshot(), time.time())
        if reason:
            raise AvitoPaused(reason)

    async def before_page(self) -> None:
        # Reserve both budget and a pacing slot atomically, before navigation.
        with self.edit() as state:
            now = time.time()
            reason = self.reason(state, now)
            if reason:
                raise AvitoPaused(reason)
            if state.get("window_until", 0) <= now:
                state.update(window_until=now + 86400, pages=0)
            slot = max(now, state.get("next_page_at", 0))
            state["next_page_at"] = slot + self.page_delay + random.uniform(0, 30)
            state["pages"] = state.get("pages", 0) + 1
        await asyncio.sleep(max(0, slot - time.time()))
        # A manual pause while waiting must also prevent navigation.
        current = self.snapshot()
        if current.get("blocked") or current.get("cooldown_until", 0) > time.time():
            raise AvitoPaused("Avito paused while waiting")

    def block(self, reason: str) -> None:
        with self.edit() as state:
            state.update(blocked=True, reason=reason, blocked_at=time.time(), probe_requested=False)

    def transient_failure(self, reason: str) -> None:
        with self.edit() as state:
            failures = state.get("failures", 0) + 1
            state.update(failures=failures, reason=reason)
            state["cooldown_until"] = max(
                state.get("cooldown_until", 0), time.time() + 3600 * 2 ** min(failures - 1, 4)
            )
            if failures >= 3:
                state["blocked"] = True

    def cooldown(self, seconds: float, reason: str) -> None:
        with self.edit() as state:
            state.update(
                cooldown_until=max(state.get("cooldown_until", 0), time.time() + seconds),
                reason=reason,
            )

    def request_probe(self) -> None:
        # Keep the block until one useful, single-page probe succeeds.
        with self.edit() as state:
            if state.get("blocked"):
                state["probe_requested"] = True

    def take_probe(self) -> bool:
        with self.edit() as state:
            if not state.get("probe_requested"):
                return False
            if state.get("cooldown_until", 0) > time.time():
                return False
            if (
                state.get("window_until", 0) > time.time()
                and state.get("pages", 0) >= self.daily_pages
            ):
                return False
            state.update(probe_requested=False, blocked=False, probe_in_progress=True)
            return True

    def finish_probe(self, useful: bool) -> None:
        with self.edit() as state:
            state.update(probe_in_progress=False, blocked=not useful)
            if useful:
                state["reason"] = "Manual verification probe succeeded"
                state["failures"] = 0

    def search_state(self, name: str) -> dict[str, Any]:
        return self.snapshot().get("searches", {}).get(name, {})

    def finish_search(self, name: str, next_page: int, interval_hours: int) -> None:
        with self.edit() as state:
            state["failures"] = 0
            state.setdefault("searches", {})[name] = {
                "next_page": next_page,
                "due_at": time.time() + interval_hours * 3600 + random.uniform(0, 1800),
            }
