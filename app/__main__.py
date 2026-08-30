from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from app.config import Settings, load_searches
from app.db import create_engine, create_session_factory
from app.logging_config import configure_logging
from app.models import Listing
from app.runner import collect_once, init_db
from services.reporting import generate_report
from services.stats import market_price_stats
from services.telegram import send_telegram

cli = typer.Typer(no_args_is_help=True)
listing_app = typer.Typer()
debug_app = typer.Typer()
cli.add_typer(listing_app, name="listing")
cli.add_typer(debug_app, name="debug")
DEFAULT_REPORT_OUTPUT = Path("data/reports/index.html")


def settings() -> Settings:
    s = Settings()
    configure_logging(s.log_level)
    return s


@cli.command("init-db")
def init_db_cmd() -> None:
    asyncio.run(init_db(settings()))


@cli.command("browser-init")
def browser_init(
    include_disabled: Annotated[
        bool,
        typer.Option("--include-disabled", help="Open disabled searches too."),
    ] = False,
    skip_searches: Annotated[
        bool,
        typer.Option("--skip-searches", help="Open only URLs passed via --url."),
    ] = False,
    url: Annotated[
        list[str] | None,
        typer.Option("--url", help="Extra URL to open."),
    ] = None,
) -> None:
    s = settings()

    async def run() -> None:
        searches = load_searches(s.searches_config_path)
        urls = [
            item.url
            for item in searches
            if not skip_searches
            and (item.enabled or include_disabled)
            and item.url != "PASTE_SEARCH_URL_HERE"
        ]
        urls.extend(url or [])
        async with async_playwright() as p:
            s.browser_profile_dir.mkdir(parents=True, exist_ok=True)
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(s.browser_profile_dir),
                headless=False,
                locale="ru-RU",
                timezone_id=s.timezone,
                viewport={"width": 1440, "height": 1000},
                args=["--ozone-platform=x11"],
            )
            for target_url in urls:
                page = await context.new_page()
                try:
                    await page.goto(target_url, wait_until="domcontentloaded")
                except PlaywrightError as exc:
                    typer.echo(f"Не удалось открыть {target_url}: {exc}")
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


@cli.command()
def report(
    days: int = typer.Option(7, min=1, help="Период отчёта в днях."),
    output: Path = typer.Option(DEFAULT_REPORT_OUTPUT, help="Куда сохранить HTML."),  # noqa: B008
) -> None:
    async def run() -> None:
        s = settings()
        engine = create_engine(s)
        session_factory = create_session_factory(engine)
        async with session_factory() as session:
            path = await generate_report(session, output_path=output, days=days)
            typer.echo(str(path.resolve()))
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
def export_html(source: str = typer.Option(...)) -> None:
    target = Path("data/debug/html") / f"{source}.html"
    typer.echo(
        f"Положите сохраненный HTML выдачи в {target} и пришлите его для настройки селекторов."
    )


if __name__ == "__main__":
    cli()
