from __future__ import annotations

from typing import Protocol

from playwright.async_api import BrowserContext

from app.models import Search
from app.schemas import ParsedListing


class CollectorBlockedError(RuntimeError):
    """Raised when a source needs user action, e.g. CAPTCHA or expired login."""


class BaseCollector(Protocol):
    source_name: str

    async def collect_search(
        self,
        search: Search,
        context: BrowserContext,
    ) -> list[ParsedListing]: ...
