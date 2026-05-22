"""Signed session cookie helpers.

The session cookie holds just the user_id; everything else is fetched
from the DB. itsdangerous signs the value with SESSION_SECRET so the
client can't forge it.
"""
from __future__ import annotations

from fastapi import Request, Response
from itsdangerous import BadSignature, TimestampSigner
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import User

SESSION_COOKIE = "mb_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def _signer() -> TimestampSigner:
    return TimestampSigner(settings.session_secret, salt="mb_session.v1")


def set_session(response: Response, user_id: int) -> None:
    token = _signer().sign(str(user_id).encode()).decode()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, samesite="lax")


def session_user_id(request: Request) -> int | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        value = _signer().unsign(raw.encode(), max_age=SESSION_MAX_AGE).decode()
        return int(value)
    except (BadSignature, ValueError):
        return None


async def current_user(request: Request, session: AsyncSession) -> User | None:
    uid = session_user_id(request)
    if uid is None:
        return None
    return await session.get(User, uid)
    # Note: we deliberately do NOT touch user.last_login_at here. Doing so
    # silently flips the row dirty and either piggybacks on an unrelated
    # commit later in the request, or gets silently dropped — making the
    # field non-deterministic. It's now updated only at OAuth login time.
