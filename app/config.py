from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://realty:change_me@postgres:5432/realty"
    browser_profile_dir: Path = Path("/data/browser-profile")
    headless: bool = True
    timezone: str = "Europe/Samara"
    searches_config_path: Path = Path("/app/config/searches.yaml")
    scoring_config_path: Path = Path("/app/config/scoring.yaml")
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    screenshots_dir: Path = Path("/app/data/debug/screenshots")
    html_dumps_dir: Path = Path("/app/data/debug/html")
    log_level: str = "INFO"


SourceName = Literal["yandex_realty", "domclick", "cian", "avito", "mirkvartir"]


class SearchConfig(BaseModel):
    name: str
    source: SourceName
    enabled: bool = True
    city: str
    rooms: int
    url: str
    interval_hours: int = Field(default=4, ge=1)
    max_pages: int = Field(default=10, ge=1, le=100)


class SearchesFile(BaseModel):
    searches: list[SearchConfig]


def load_searches(path: Path) -> list[SearchConfig]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return SearchesFile.model_validate(data).searches


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
