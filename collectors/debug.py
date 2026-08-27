from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from playwright.async_api import Page


class DebugMixin:
    debug_run_id: UUID | None = None
    debug_screenshots_dir: Path | None = None
    debug_html_dir: Path | None = None
    last_debug_screenshot_path: str | None = None
    last_debug_html_path: str | None = None

    async def save_debug_page(self, page: Page) -> None:
        if not self.debug_run_id or not self.debug_screenshots_dir or not self.debug_html_dir:
            return
        self.debug_screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.debug_html_dir.mkdir(parents=True, exist_ok=True)
        source = getattr(self, "source_name", "unknown")
        screenshot = self.debug_screenshots_dir / f"{self.debug_run_id}_{source}.png"
        html = self.debug_html_dir / f"{self.debug_run_id}_{source}.html"
        await page.screenshot(path=str(screenshot), full_page=True)
        html.write_text(await page.content(), encoding="utf-8")
        self.last_debug_screenshot_path = str(screenshot)
        self.last_debug_html_path = str(html)


def setup_debug(collector: Any, *, run_id: UUID, screenshots_dir: Path, html_dir: Path) -> None:
    if hasattr(collector, "debug_run_id"):
        collector.debug_run_id = run_id
        collector.debug_screenshots_dir = screenshots_dir
        collector.debug_html_dir = html_dir
        collector.last_debug_screenshot_path = None
        collector.last_debug_html_path = None
