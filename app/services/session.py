"""Signed session cookie helpers.

The session cookie holds `{user_id}:{jti}` signed with SESSION_SECRET.
The `jti` is a per-session random ID stored on the user row; the
middleware rejects any cookie whose jti doesn't match the user's
current value. That gives us:

  - session fixation defence (a planted cookie value becomes invalid
    the moment the victim logs in — login mints a fresh jti)
  - server-side logout invalidation (clearing the cookie isn't enough
    if the attacker copied it; nulling user.session_jti is)
  - "log out everywhere" knob (set jti back to None)

The cookie salt is bumped to v2 with the jti format change. Sessions
created against v1 (no jti, plain str(user_id)) unsign successfully
against v2 only if we used the same salt — we deliberately changed
the salt so all v1 sessions are invalidated and force re-login once.
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
    return TimestampSigner(settings.session_secret, salt="mb_session.v2")


def set_session(response: Response, user_id: int, jti: str) -> None:
    """Stamp a fresh signed session cookie embedding `user_id:jti`."""
    payload = f"{user_id}:{jti}".encode()
    token = _signer().sign(payload).decode()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        samesite="lax",
        secure=settings.secure_cookies,
        httponly=True,
        path="/",
    )


def parse_session(request: Request) -> tuple[int, str] | None:
    """Decode and verify the cookie signature. Returns `(user_id, jti)`
    or None. Does NOT cross-check the jti against the user row — that's
    the caller's job (so we don't need a DB session here)."""
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        value = _signer().unsign(raw.encode(), max_age=SESSION_MAX_AGE).decode()
    except (BadSignature, ValueError):
        return None
    if ":" not in value:
        return None  # legacy v1 format — force re-login
    uid_s, jti = value.split(":", 1)
    try:
        return int(uid_s), jti
    except ValueError:
        return None


def session_user_id(request: Request) -> int | None:
    """LEGACY helper — returns user_id from a validly-signed cookie
    WITHOUT the jti cross-check. Only used by the middleware's quick
    early-out path before it hits the DB; the subsequent `current_user`
    call does the full check."""
    parsed = parse_session(request)
    return parsed[0] if parsed else None


async def current_user(request: Request, session: AsyncSession) -> User | None:
    """The authoritative auth check: validates the cookie signature
    AND verifies the embedded jti matches the user's current
    session_jti. Either mismatch returns None (treated as logged-out)."""
    parsed = parse_session(request)
    if parsed is None:
        return None
    uid, jti = parsed
    user = await session.get(User, uid)
    if user is None or user.session_jti is None or user.session_jti != jti:
        return None
    return user
