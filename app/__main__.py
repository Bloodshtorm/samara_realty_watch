from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from playwright.async_api import async_playwright

from app.config import Settings, load_searches
from app.db import create_engine, create_session_factory
from app.logging_config import configure_logging
from app.models import Listing
from app.runner import collect_once, init_db
from services.stats import market_price_stats
from services.telegram import send_telegram

cli = typer.Typer(no_args_is_help=True)
listing_app = typer.Typer()
debug_app = typer.Typer()
cli.add_typer(listing_app, name="listing")
cli.add_typer(debug_app, name="debug")


def settings() -> Settings:
    s = Settings()
    configure_logging(s.log_level)
    return s


@cli.command("init-db")
def init_db_cmd() -> None:
    asyncio.run(init_db(settings()))


@cli.command("browser-init")
def browser_init() -> None:
    s = settings()

    async def run() -> None:
        searches = load_searches(s.searches_config_path)
        async with async_playwright() as p:
            s.browser_profile_dir.mkdir(parents=True, exist_ok=True)
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(s.browser_profile_dir),
                headless=False,
                locale="ru-RU",
                timezone_id=s.timezone,
                viewport={"width": 1440, "height": 1000},
            )
            for item in searches:
                if item.enabled and item.url != "PASTE_SEARCH_URL_HERE":
                    page = await context.new_page()
                    await page.goto(item.url, wait_until="domcontentloaded")
            typer.echo("Войдите на сайты в открытом Chromium. После завершения нажмите Ctrl+C.")
            try:
                await asyncio.Event().wait()
            finally:
                await context.close()

    asyncio.run(run())


@cli.command()
def collect(
    source: str | None = typer.Option(None),
    search: str | None = typer.Option(None),
) -> None:
    asyncio.run(collect_once(settings(), only_source=source, only_search=search))


@cli.command()
def stats(district: str | None = typer.Option(None)) -> None:
    async def run() -> None:
        s = settings()
        engine = create_engine(s)
        session_factory = create_session_factory(engine)
        async with session_factory() as session:
            result = await market_price_stats(session, district=district)
            typer.echo(json.dumps(result, ensure_ascii=False))
        await engine.dispose()

    asyncio.run(run())


@listing_app.command("show")
def listing_show(id: str) -> None:
    async def run() -> None:
        s = settings()
        engine = create_engine(s)
        session_factory = create_session_factory(engine)
        async with session_factory() as session:
            listing = await session.get(Listing, id)
            if listing is None:
                raise typer.Exit(1)
            typer.echo(
                json.dumps(
                    {
                        "id": str(listing.id),
                        "source": listing.source,
                        "url": listing.url,
                        "price_rub": listing.price_rub,
                        "address": listing.address_normalized,
                        "score": listing.score,
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
        await engine.dispose()

    asyncio.run(run())


@listing_app.command("duplicates")
def listing_duplicates() -> None:
    typer.echo("Команда зарезервирована: probable duplicates сохраняются в listing_links.")


@cli.command("notify-test")
def notify_test() -> None:
    async def run() -> None:
        ok, error = await send_telegram(settings(), "Тест realty collector")
        typer.echo("ok" if ok else f"failed: {error}")

    asyncio.run(run())


@debug_app.command("export-html")
def export_html(source: str) -> None:
    target = Path("data/debug/html") / f"{source}.html"
    typer.echo(
        f"Положите сохраненный HTML выдачи в {target} и пришлите его для настройки селекторов."
    )


if __name__ == "__main__":
    cli()
