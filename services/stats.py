from __future__ import annotations

from statistics import median, quantiles

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Listing


async def market_price_stats(
    session: AsyncSession,
    *,
    district: str | None = None,
) -> dict[str, int | float | None]:
    stmt = select(Listing.price_per_m2).where(
        Listing.is_active.is_(True),
        Listing.rooms == 3,
        Listing.price_per_m2.is_not(None),
    )
    if district:
        stmt = stmt.where(Listing.district == district)
    values = [int(row[0]) for row in (await session.execute(stmt)).all()]
    if not values:
        return {"count": 0, "median_price_per_m2": None, "p25": None, "p75": None}
    quartiles = quantiles(values, n=4) if len(values) >= 4 else [None, None, None]
    return {
        "count": len(values),
        "median_price_per_m2": int(median(values)),
        "p25": None if quartiles[0] is None else int(quartiles[0]),
        "p75": None if quartiles[2] is None else int(quartiles[2]),
    }
