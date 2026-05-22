"""Anonymous-poster identity via opaque cookie token.

No accounts: anyone visiting the forum gets a random token in the
`mb_token` cookie. The token is used to:
  - enforce 1 upvote per thread per visitor
  - let a poster delete or edit their own post within a short window
  - drive per-token rate-limits

Tokens are not authentication — clearing cookies or using a private
window starts a new identity. That's accepted for this MVP.
"""
from __future__ import annotations

import secrets

from fastapi import Request, Response

from app.config import settings

COOKIE_NAME = "mb_token"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year


def get_or_create_token(request: Request, response: Response) -> str:
    token = request.cookies.get(COOKIE_NAME)
    if token and len(token) >= 24:
        return token
    token = secrets.token_urlsafe(24)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
    )
    return token


def get_token(request: Request) -> str | None:
    token = request.cookies.get(COOKIE_NAME)
    return token if token and len(token) >= 24 else None
