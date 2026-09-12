from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, SupportsFloat
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Listing, ListingAIReview, SearchContext, User

MAX_DESCRIPTION_CHARS = 1200
MAX_TEXT_ITEMS = 6
VERDICTS = {"watch", "maybe", "skip"}


class AIClient(Protocol):
    async def review_listing(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class OllamaClient:
    base_url: str
    model: str
    prompt_version: str
    timeout_seconds: float = 120.0

    async def review_listing(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = {
            "model": self.model,
            "format": "json",
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты локальный аналитик по покупке квартир в Самаре. "
                        "Фильтры и hard rules уже применены кодом. Не выдумывай факты, "
                        "оцени только переданный вариант и верни строго JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "prompt_version": self.prompt_version,
                            "task": (
                                "Оцени квартиру для просмотра. Верни JSON с полями: "
                                "ai_score integer 0..100, verdict one of watch/maybe/skip, "
                                "pros array, cons array, risks array, summary string."
                            ),
                            "payload": payload,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            ],
        }
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=self.timeout_seconds
        ) as client:
            response = await client.post("/api/chat", json=request)
            response.raise_for_status()
            data = response.json()
        message = data.get("message") if isinstance(data, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ValueError("Ollama response does not contain message.content")
        return parse_ai_response(content)


def compact_listing_payload(listing: Listing, context: SearchContext) -> dict[str, Any]:
    features = listing.features if isinstance(listing.features, dict) else {}
    safe_features = {
        key: value
        for key, value in features.items()
        if key
        in {
            "mortgage_available",
            "family_mortgage",
            "it_mortgage",
            "subsidized_mortgage",
            "coordinates_inferred",
            "location_confidence",
        }
    }
    return {
        "listing": {
            "source": listing.source,
            "source_listing_id": listing.source_listing_id,
            "group_id": str(listing.group_id) if listing.group_id else None,
            "title": _clean_text(listing.title, 300),
            "address": _clean_text(listing.address_normalized or listing.address_raw, 300),
            "district": listing.district,
            "rooms": listing.rooms,
            "area_total_m2": _number(listing.area_total_m2),
            "area_living_m2": _number(listing.area_living_m2),
            "area_kitchen_m2": _number(listing.area_kitchen_m2),
            "price_rub": listing.price_rub,
            "price_per_m2": listing.price_per_m2,
            "floor": listing.floor,
            "floors_total": listing.floors_total,
            "building_year": listing.building_year,
            "building_type": listing.building_type,
            "seller_type": listing.seller_type,
            "photos_count": listing.photos_count,
            "description": _clean_text(listing.description, MAX_DESCRIPTION_CHARS),
            "deterministic_score": listing.score,
            "deterministic_reasons": _text_list(listing.score_reasons),
            "features": safe_features,
        },
        "context": {
            "id": str(context.id),
            "slug": context.slug,
            "name": context.name,
            "object_type": context.object_type,
            "city": context.city,
            "expected_rooms": context.expected_rooms,
            "rules": context.rules or {},
        },
    }


def input_hash(payload: dict[str, Any], *, model_name: str, prompt_version: str) -> str:
    normalized = json.dumps(
        {"model_name": model_name, "payload": payload, "prompt_version": prompt_version},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_ai_response(content: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match is None:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("AI response must be a JSON object")
    score = parsed.get("ai_score")
    if not isinstance(score, int):
        raise ValueError("AI response ai_score must be an integer")
    parsed["ai_score"] = max(0, min(score, 100))
    verdict = parsed.get("verdict")
    parsed["verdict"] = verdict if isinstance(verdict, str) and verdict in VERDICTS else "maybe"
    parsed["pros"] = _text_list(parsed.get("pros"))
    parsed["cons"] = _text_list(parsed.get("cons"))
    parsed["risks"] = _text_list(parsed.get("risks"))
    parsed["summary"] = _clean_text(parsed.get("summary"), 2000) or ""
    return parsed


async def latest_reviews_for_listings(
    session: AsyncSession,
    listing_ids: list[UUID],
    *,
    user: User,
    context: SearchContext,
) -> dict[UUID, ListingAIReview]:
    if not listing_ids:
        return {}
    rows = (
        await session.scalars(
            select(ListingAIReview)
            .where(
                ListingAIReview.user_id == user.id,
                ListingAIReview.context_id == context.id,
                ListingAIReview.listing_id.in_(listing_ids),
            )
            .order_by(ListingAIReview.updated_at.desc(), ListingAIReview.created_at.desc())
        )
    ).all()
    result: dict[UUID, ListingAIReview] = {}
    for row in rows:
        result.setdefault(row.listing_id, row)
    return result


async def review_listing_with_cache(
    session: AsyncSession,
    *,
    listing: Listing,
    context: SearchContext,
    user: User,
    model_name: str,
    prompt_version: str,
    client: AIClient,
    force: bool = False,
) -> tuple[ListingAIReview, bool]:
    payload = compact_listing_payload(listing, context)
    digest = input_hash(payload, model_name=model_name, prompt_version=prompt_version)
    if not force:
        cached = (
            await session.scalars(
                select(ListingAIReview).where(
                    ListingAIReview.user_id == user.id,
                    ListingAIReview.context_id == context.id,
                    ListingAIReview.listing_id == listing.id,
                    ListingAIReview.model_name == model_name,
                    ListingAIReview.prompt_version == prompt_version,
                    ListingAIReview.input_hash == digest,
                )
            )
        ).first()
        if cached is not None:
            return cached, False
    parsed = await client.review_listing(payload)
    now = datetime.now(UTC)
    review = ListingAIReview(
        user_id=user.id,
        context_id=context.id,
        listing_id=listing.id,
        group_id=listing.group_id,
        model_name=model_name,
        prompt_version=prompt_version,
        input_hash=digest,
        ai_score=parsed["ai_score"],
        verdict=parsed["verdict"],
        pros=parsed["pros"],
        cons=parsed["cons"],
        risks=parsed["risks"],
        summary=parsed["summary"],
        raw_response=parsed,
        created_at=now,
        updated_at=now,
    )
    session.add(review)
    await session.flush()
    return review, True


async def review_listings(
    session: AsyncSession,
    *,
    listings: list[Listing],
    context: SearchContext,
    user: User,
    model_name: str,
    prompt_version: str,
    client: AIClient,
    force: bool = False,
) -> dict[str, int]:
    created = 0
    reused = 0
    for listing in listings:
        _, is_created = await review_listing_with_cache(
            session,
            listing=listing,
            context=context,
            user=user,
            model_name=model_name,
            prompt_version=prompt_version,
            client=client,
            force=force,
        )
        if is_created:
            created += 1
        else:
            reused += 1
    return {"created": created, "reused": reused, "total": len(listings)}


def _clean_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        return None
    return cleaned[:limit]


def _number(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, SupportsFloat):
        return None
    return float(value)


def _text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        cleaned = _clean_text(item, 300)
        if cleaned:
            result.append(cleaned)
    return result[:MAX_TEXT_ITEMS]
