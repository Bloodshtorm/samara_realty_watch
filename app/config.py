from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://realty:change_me@postgres:5432/realty"
    browser_profile_dir: Path = Path("/data/browser-profile")
    browser_channel: str | None = None
    headless: bool = True
    timezone: str = "Europe/Samara"
    searches_config_path: Path = Path("/app/config/searches.yaml")
    scoring_config_path: Path = Path("/app/config/scoring.yaml")
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    screenshots_dir: Path = Path("/app/data/debug/screenshots")
    html_dumps_dir: Path = Path("/app/data/debug/html")
    log_level: str = "INFO"


SourceName = Literal["yandex_realty", "domclick", "cian", "avito", "mirkvartir", "n1", "etagi"]
ObjectType = Literal["flat", "land"]
DEFAULT_CONTEXT_SLUG = "3rooms_samara"


class SearchContextConfig(BaseModel):
    slug: str
    name: str
    object_type: ObjectType = "flat"
    city: str = "Самара"
    expected_rooms: int | None = None
    center_latitude: float = 53.195873
    center_longitude: float = 50.100193
    radius_km: float | None = None
    enabled: bool = True
    rules: dict[str, Any] = Field(default_factory=dict)


class SearchConfig(BaseModel):
    name: str
    source: SourceName
    context_slug: str = DEFAULT_CONTEXT_SLUG
    enabled: bool = True
    city: str
    rooms: int | None = None
    url: str
    interval_hours: int = Field(default=4, ge=1)
    max_pages: int = Field(default=10, ge=1, le=100)


class SearchesFile(BaseModel):
    contexts: list[SearchContextConfig] = Field(default_factory=list)
    searches: list[SearchConfig]


def load_search_config(path: Path) -> SearchesFile:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    parsed = SearchesFile.model_validate(data)
    if parsed.contexts:
        return parsed
    return parsed.model_copy(
        update={
            "contexts": [
                SearchContextConfig(
                    slug=DEFAULT_CONTEXT_SLUG,
                    name="3-комнатные квартиры в Самаре",
                    expected_rooms=3,
                    rules={
                        "exclude_fractional_shares": True,
                        "exclude_low_rise_max_floors": 5,
                        "exclude_settlements": True,
                    },
                )
            ]
        }
    )


def load_searches(path: Path) -> list[SearchConfig]:
    return load_search_config(path).searches


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
