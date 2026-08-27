from __future__ import annotations

from datetime import datetime


def format_rub(value: int | float | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.0f}".replace(",", " ") + " ₽"


def format_m2(value: int | float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.1f} м²"


def format_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M")


def format_percent(value: float | None) -> str:
    if value is None:
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"
