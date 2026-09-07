from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import (
    ApartmentGroup,
    ApartmentUserState,
    ListingUserState,
    SearchContext,
    User,
    UserSession,
)

PBKDF2_ITERATIONS = 240_000
SESSION_COOKIE_NAME = "srw_session"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt, expected = password_hash.split("$", 3)
        iterations = int(iterations_raw)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), iterations
    ).hex()
    return hmac.compare_digest(digest, expected)


def new_session_token() -> str:
    return secrets.token_urlsafe(48)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def bootstrap_admin(session: AsyncSession, settings: Settings) -> User | None:
    users_count = await session.scalar(select(func.count()).select_from(User))
    if users_count:
        admin = (
            await session.execute(
                select(User).where(User.role == "admin").order_by(User.created_at)
            )
        ).scalars().first()
        if admin is not None:
            await assign_unowned_contexts(session, admin)
        return admin
    if not settings.app_admin_username or not settings.app_admin_password:
        return None
    admin = User(
        username=settings.app_admin_username.strip(),
        display_name=(settings.app_admin_display_name or settings.app_admin_username).strip(),
        role="admin",
        password_hash=hash_password(settings.app_admin_password),
        is_active=True,
    )
    session.add(admin)
    await session.flush()
    await assign_unowned_contexts(session, admin)
    return admin


async def assign_unowned_contexts(session: AsyncSession, owner: User) -> None:
    contexts = (
        await session.execute(select(SearchContext).where(SearchContext.owner_user_id.is_(None)))
    ).scalars()
    for context in contexts:
        context.owner_user_id = owner.id
    listing_states = (
        await session.execute(select(ListingUserState).where(ListingUserState.user_id.is_(None)))
    ).scalars()
    for state in listing_states:
        state.user_id = owner.id
    flagged_groups = (
        await session.execute(
            select(ApartmentGroup).where(
                (ApartmentGroup.is_favorite.is_(True)) | (ApartmentGroup.is_hidden.is_(True))
            )
        )
    ).scalars()
    for group in flagged_groups:
        exists_state = (
            await session.execute(
                select(ApartmentUserState).where(
                    ApartmentUserState.user_id == owner.id,
                    ApartmentUserState.group_id == group.id,
                )
            )
        ).scalar_one_or_none()
        if exists_state is None:
            session.add(
                ApartmentUserState(
                    user_id=owner.id,
                    group_id=group.id,
                    is_favorite=group.is_favorite,
                    is_hidden=group.is_hidden,
                )
            )


async def create_user_session(
    session: AsyncSession, user: User, *, days: int
) -> tuple[UserSession, str]:
    token = new_session_token()
    now = datetime.now(UTC)
    user_session = UserSession(
        user_id=user.id,
        token_hash=hash_session_token(token),
        expires_at=now + timedelta(days=days),
        last_seen_at=now,
    )
    session.add(user_session)
    return user_session, token
