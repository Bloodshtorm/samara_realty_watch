from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from statistics import median

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Listing, PriceHistory
from app.reporting_format import format_dt, format_m2, format_percent, format_rub


def format_value(value: object | None) -> str:
    if value is None:
        return "-"
    return escape(str(value))


async def generate_report(session: AsyncSession, output_path: Path, days: int = 7) -> Path:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    listing_rows = await session.execute(
        select(Listing)
        .where(
            Listing.is_active.is_(True),
            Listing.last_seen_at >= cutoff,
        )
        .order_by(Listing.price_rub.asc().nulls_last(), Listing.last_seen_at.desc())
    )
    listings = list(listing_rows.scalars().all())
    change_rows = await session.execute(
        select(PriceHistory, Listing)
        .join(Listing, PriceHistory.listing_id == Listing.id)
        .where(
            Listing.is_active.is_(True),
            PriceHistory.observed_at >= cutoff,
        )
        .order_by(PriceHistory.observed_at.desc())
    )
    changes = [(row[0], row[1]) for row in change_rows.all()]

    prices = [item.price_rub for item in listings if item.price_rub is not None]
    prices_m2 = [item.price_per_m2 for item in listings if item.price_per_m2 is not None]
    generated_at = datetime.now(UTC)

    html = _render_html(
        listings=listings,
        changes=changes,
        days=days,
        generated_at=generated_at,
        min_price=min(prices) if prices else None,
        median_price=int(median(prices)) if prices else None,
        median_price_m2=int(median(prices_m2)) if prices_m2 else None,
    )
    await asyncio.to_thread(_write_report, output_path, html)
    return output_path


def _write_report(output_path: Path, html: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")


def _render_html(
    *,
    listings: list[Listing],
    changes: list[tuple[PriceHistory, Listing]],
    days: int,
    generated_at: datetime,
    min_price: int | None,
    median_price: int | None,
    median_price_m2: int | None,
) -> str:
    listing_rows = "\n".join(_listing_row(item) for item in listings)
    if not listing_rows:
        listing_rows = '<tr><td colspan="10" class="empty">За период объявлений нет.</td></tr>'

    change_rows = "\n".join(_change_row(change, listing) for change, listing in changes)
    if not change_rows:
        change_rows = (
            '<tr><td colspan="7" class="empty">Пока изменений цен не найдено. '
            "Они появятся после повторных сборов, если цена объявления изменится.</td></tr>"
        )

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Samara Realty Watch</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f6f8;
      --panel: #ffffff;
      --text: #19202a;
      --muted: #657080;
      --line: #d9dee7;
      --accent: #0f766e;
      --danger: #b42318;
      --ok: #067647;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{ max-width: 1280px; margin: 0 auto; padding: 28px 20px 40px; }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: end;
      margin-bottom: 22px;
    }}
    h1 {{ margin: 0; font-size: 28px; line-height: 1.15; }}
    h2 {{ margin: 28px 0 12px; font-size: 18px; }}
    .muted {{ color: var(--muted); }}
    .cards {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }}
    .label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .04em;
    }}
    .value {{ margin-top: 4px; font-size: 22px; font-weight: 700; }}
    .table-wrap {{
      overflow-x: auto;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    table {{ width: 100%; min-width: 980px; border-collapse: collapse; }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
      text-align: left;
    }}
    th {{
      background: #eef2f6;
      color: #344054;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .03em;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    a {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
    .num {{ white-space: nowrap; font-variant-numeric: tabular-nums; }}
    .addr {{ max-width: 330px; }}
    .badge {{
      display: inline-block;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 8px;
      color: var(--muted);
    }}
    .up {{ color: var(--danger); font-weight: 700; }}
    .down {{ color: var(--ok); font-weight: 700; }}
    .empty {{ color: var(--muted); text-align: center; padding: 22px; }}
    @media (max-width: 800px) {{
      header {{ display: block; }}
      .cards {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Samara Realty Watch</h1>
        <div class="muted">Объявления, увиденные за последние {days} дней</div>
      </div>
      <div class="muted">Сформировано: {format_dt(generated_at)}</div>
    </header>

    <section class="cards">
      <div class="card">
        <div class="label">Объявлений</div>
        <div class="value">{len(listings)}</div>
      </div>
      <div class="card">
        <div class="label">Мин. цена</div>
        <div class="value">{format_rub(min_price)}</div>
      </div>
      <div class="card">
        <div class="label">Медиана</div>
        <div class="value">{format_rub(median_price)}</div>
      </div>
      <div class="card">
        <div class="label">Медиана за м²</div>
        <div class="value">{format_rub(median_price_m2)}</div>
      </div>
    </section>

    <h2>Варианты</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Цена</th>
            <th>₽/м²</th>
            <th>Площадь</th>
            <th>Этаж</th>
            <th>Адрес / заголовок</th>
            <th>Район</th>
            <th>Источник</th>
            <th>Первый раз</th>
            <th>Последний раз</th>
            <th>Ссылка</th>
          </tr>
        </thead>
        <tbody>{listing_rows}</tbody>
      </table>
    </div>

    <h2>Изменения цен</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Дата</th>
            <th>Объявление</th>
            <th>Было</th>
            <th>Стало</th>
            <th>Разница</th>
            <th>%</th>
            <th>Ссылка</th>
          </tr>
        </thead>
        <tbody>{change_rows}</tbody>
      </table>
    </div>
  </main>
</body>
</html>
"""


def _listing_row(item: Listing) -> str:
    floor = "-"
    if item.floor and item.floors_total:
        floor = f"{item.floor}/{item.floors_total}"
    elif item.floor:
        floor = str(item.floor)
    address = item.address_normalized or item.address_raw or item.title or "-"
    return f"""<tr>
  <td class="num">{format_rub(item.price_rub)}</td>
  <td class="num">{format_rub(item.price_per_m2)}</td>
  <td class="num">{format_m2(item.area_total_m2)}</td>
  <td class="num">{escape(floor)}</td>
  <td class="addr">{escape(address)}</td>
  <td>{format_value(item.district)}</td>
  <td><span class="badge">{escape(item.source)}</span></td>
  <td class="num">{format_dt(item.first_seen_at)}</td>
  <td class="num">{format_dt(item.last_seen_at)}</td>
  <td><a href="{escape(item.url)}" target="_blank" rel="noreferrer">Открыть</a></td>
</tr>"""


def _change_row(change: PriceHistory, listing: Listing) -> str:
    css = "down" if (change.change_rub or 0) < 0 else "up"
    title = listing.address_normalized or listing.address_raw or listing.title or str(listing.id)
    return f"""<tr>
  <td class="num">{format_dt(change.observed_at)}</td>
  <td class="addr">{escape(title)}</td>
  <td class="num">{format_rub(change.old_price_rub)}</td>
  <td class="num">{format_rub(change.new_price_rub)}</td>
  <td class="num {css}">{format_rub(change.change_rub)}</td>
  <td class="num {css}">{format_percent(change.change_percent)}</td>
  <td><a href="{escape(listing.url)}" target="_blank" rel="noreferrer">Открыть</a></td>
</tr>"""
