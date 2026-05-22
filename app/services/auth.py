"""Admin auth — signed cookie + form login.

Replaces the older HTTP Basic flow. The form at /auth/login posts
credentials to /auth/admin/login which verifies against settings and
stamps a signed `mb_admin` cookie. Middleware enforces presence of
the cookie on /admin/* paths and redirects to /auth/login otherwise.
"""
from __future__ import annotations

import secrets

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from app.config import settings

ADMIN_COOKIE = "mb_admin"
ADMIN_COOKIE_MAX_AGE = 60 * 60 * 8  # 8 hours


def _signer() -> TimestampSigner:
    return TimestampSigner(settings.session_secret, salt="mb_admin.v2")


def verify_admin_credentials(username: str, password: str) -> bool:
    return (
        secrets.compare_digest(username, settings.admin_username)
        and secrets.compare_digest(password, settings.admin_password)
    )


def set_admin_session(response: Response) -> None:
    token = _signer().sign(b"admin").decode()
    response.set_cookie(
        ADMIN_COOKIE,
        token,
        max_age=ADMIN_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
    )


def clear_admin_session(response: Response) -> None:
    response.delete_cookie(ADMIN_COOKIE, samesite="lax")


def is_admin(request: Request) -> bool:
    raw = request.cookies.get(ADMIN_COOKIE)
    if not raw:
        return False
    try:
        _signer().unsign(raw.encode(), max_age=ADMIN_COOKIE_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


def admin_marker_present(request: Request) -> bool:
    """Back-compat alias used in a few spots (forum DEV label decisions)."""
    return is_admin(request)
