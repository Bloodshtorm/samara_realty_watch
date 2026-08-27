from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from playwright.async_api import Page


async def save_debug_artifacts(
    *,
    page: Page,
    screenshots_dir: Path,
    html_dumps_dir: Path,
    run_id: uuid.UUID,
    source: str,
) -> tuple[str, str]:
    await asyncio.to_thread(screenshots_dir.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(html_dumps_dir.mkdir, parents=True, exist_ok=True)
    screenshot_path = screenshots_dir / f"{run_id}_{source}.png"
    html_path = html_dumps_dir / f"{run_id}_{source}.html"
    await page.screenshot(path=str(screenshot_path), full_page=True)
    await asyncio.to_thread(html_path.write_text, await page.content(), encoding="utf-8")
    return str(screenshot_path), str(html_path)
