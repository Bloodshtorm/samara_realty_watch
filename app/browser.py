from __future__ import annotations

from collections.abc import AsyncIterator

from playwright.async_api import BrowserContext, async_playwright

from app.config import Settings


async def persistent_context(
    settings: Settings, *, headless: bool | None = None
) -> AsyncIterator[BrowserContext]:
    async with async_playwright() as p:
        if settings.browser_cdp_url:
            browser = await p.chromium.connect_over_cdp(settings.browser_cdp_url)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            yield context
            return
        settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(settings.browser_profile_dir),
            channel=settings.browser_channel,
            headless=settings.headless if headless is None else headless,
            locale="ru-RU",
            timezone_id=settings.timezone,
            viewport={"width": 1440, "height": 1000},
            args=["--ozone-platform=x11"],
        )
        try:
            yield context
        finally:
            await context.close()
