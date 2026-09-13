import asyncio
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import select

from app.config import Settings
from app.db import create_engine, create_session_factory
from app.models import AIReviewJob, Listing, SearchContext, User
from services.ai_recommendations import OllamaClient, review_listing_with_cache
from services.listing_access import visible_listing_condition

_model_slot = asyncio.Semaphore(1)


async def run_ai_job(job_id: UUID, database_url: str) -> None:
    settings = Settings(database_url=database_url)
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with _model_slot, factory() as session:
            job = await session.get(AIReviewJob, job_id)
            if job is None:
                return
            user = await session.get(User, job.user_id)
            context = await session.get(SearchContext, job.context_id)
            if user is None or not user.is_active or context is None or not context.enabled:
                raise ValueError("Контекст или пользователь недоступен")
            if user.role != "admin" and context.owner_user_id != user.id:
                raise ValueError("Контекст больше недоступен")
            listing = await session.scalar(
                select(Listing).where(Listing.id == job.listing_id, visible_listing_condition(user))
            )
            if listing is None:
                raise ValueError("Объявление больше недоступно")
            job.status = "running"
            await session.commit()
            await review_listing_with_cache(
                session,
                listing=listing,
                context=context,
                user=user,
                model_name=settings.ollama_model,
                prompt_version=settings.ai_prompt_version,
                client=OllamaClient(
                    settings.ollama_base_url,
                    settings.ollama_model,
                    settings.ai_prompt_version,
                    timeout_seconds=240,
                ),
                force=True,
            )
            job.status, job.updated_at = "completed", datetime.now(UTC)
            await session.commit()
    except Exception as exc:
        structlog.get_logger(__name__).error(
            "ai_job_failed", job_id=str(job_id), error_type=type(exc).__name__
        )
        async with factory() as session:
            job = await session.get(AIReviewJob, job_id)
            if job:
                job.status = "failed"
                job.error = "Анализ не завершён. Проверьте доступность модели и повторите запуск."
                await session.commit()
    finally:
        await engine.dispose()
