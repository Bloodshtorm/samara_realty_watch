from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright
from sqlalchemy import MetaData, Table, select

from app.config import Settings, load_searches
from app.db import create_engine, create_session_factory
from app.logging_config import configure_logging
from app.models import Listing
from app.runner import collect_once, init_db
from services.apartments import candidate_pairs, reconcile_groups
from services.deduplication import compare_listings
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
    due_only: bool = typer.Option(
        False, help="Run searches whose configured interval has elapsed."
    ),
) -> None:
    asyncio.run(collect_once(settings(), only_source=source, only_search=search, due_only=due_only))


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
def listing_duplicates(apply: bool = typer.Option(False, "--apply")) -> None:
    """Preview duplicate candidates; --apply persists apartment groups after migration."""

    async def run() -> None:
        engine = create_engine(settings())
        try:
            factory = create_session_factory(engine)
            if apply:
                async with factory() as session, session.begin():
                    typer.echo(json.dumps(await reconcile_groups(session)))
                return
            # Reflection allows a read-only preview before installing the new schema.
            async with engine.connect() as conn:
                table = await conn.run_sync(
                    lambda sync: Table("listings", MetaData(), autoload_with=sync)
                )
                rows = (await conn.execute(select(table))).mappings().all()
            from uuid import UUID

            items = []
            for row in rows:
                values = dict(row)
                for name in ("id", "group_id"):
                    if values.get(name) and not isinstance(values[name], UUID):
                        values[name] = UUID(values[name])
                items.append(Listing(**values))
            by_id = {item.id: item for item in items}
            pairs = candidate_pairs(items)
            matches = {key: compare_listings(by_id[key[0]], by_id[key[1]]) for key in pairs}
            groups = {item.id: [item.id] for item in items}
            owner = {item.id: item.id for item in items}
            from services.apartments import pair_key

            for key in sorted(pairs, key=str):
                left, right = (owner[ident] for ident in key)
                if left == right:
                    continue
                if all(
                    (match := matches.get(pair_key(a, b))) and match.automatic
                    for a in groups[left]
                    for b in groups[right]
                ):
                    for ident in groups[right]:
                        owner[ident] = left
                    groups[left].extend(groups.pop(right))
            output = [
                [
                    {
                        "id": str(ident),
                        "source": by_id[ident].source,
                        "address": by_id[ident].address_normalized,
                        "area": float(by_id[ident].area_total_m2 or 0),
                    }
                    for ident in members
                ]
                for members in groups.values()
                if len(members) > 1
            ]
            typer.echo(
                json.dumps(
                    {
                        "read_only": True,
                        "compared_pairs": len(pairs),
                        "groups": output,
                        "candidate_pairs": sum(
                            bool(match and not match.automatic) for match in matches.values()
                        ),
                    },
                    ensure_ascii=False,
                )
            )
        finally:
            await engine.dispose()

    asyncio.run(run())


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
