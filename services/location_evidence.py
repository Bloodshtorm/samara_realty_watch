from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Listing, LocationEvidence
from services.geography import distance_km, usable_coordinates


async def nearby_stops(session: AsyncSession, listing: Listing) -> dict:
    lat, lon = listing.latitude, listing.longitude
    if lat is None or lon is None or not usable_coordinates(lat, lon):
        return {"status": "unknown", "reason": "Нет достоверных координат квартиры"}
    lat, lon = round(float(lat), 5), round(float(lon), 5)
    key = f"stops-v1:{lat}:{lon}"
    cached = await session.get(LocationEvidence, key)
    now = datetime.now(UTC)
    if cached and now - cached.updated_at.replace(tzinfo=UTC) < timedelta(
        days=7 if cached.payload.get("status") == "ok" else 0, minutes=30
    ):
        return cached.payload
    query = (
        f"[out:json][timeout:15];(node(around:1000,{lat},{lon})[highway=bus_stop];"
        f"node(around:1000,{lat},{lon})[railway=tram_stop];"
        f"nwr(around:1000,{lat},{lon})[public_transport=platform];);out center 100;"
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://overpass-api.de/api/interpreter", data={"data": query}
            )
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict) or data.get("remark"):
            raise ValueError("Incomplete Overpass response")
        stops: list[dict] = []
        for element in data.get("elements", []):
            coords = element.get("center", element)
            if not usable_coordinates(coords.get("lat"), coords.get("lon")):
                continue
            distance = round(distance_km(lat, lon, coords["lat"], coords["lon"]) * 1000)
            if distance > 1000:
                continue
            stops.append(
                {
                    "name": str(element.get("tags", {}).get("name", "Остановка без названия"))[
                        :150
                    ],
                    "distance_m_straight_line": distance,
                    "url": f"https://www.openstreetmap.org/{element['type']}/{int(element['id'])}",
                }
            )
        payload = {
            "status": "ok",
            "source": "OpenStreetMap / Overpass",
            "checked_at": now.isoformat(),
            "radius_m": 1000,
            "stops": sorted(stops, key=lambda s: s["distance_m_straight_line"])[:10],
            "caveat": "Расстояние по прямой, не маршрут пешком. Карта может быть неполной.",
            "coordinates_inferred": bool((listing.features or {}).get("coordinates_inferred")),
        }
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        payload = {
            "status": "unavailable",
            "reason": "Сервис остановок временно недоступен; близость не подтверждена",
        }
    if cached:
        cached.payload, cached.updated_at = payload, now
    else:
        session.add(LocationEvidence(key=key, payload=payload, updated_at=now))
    await session.flush()
    return payload
