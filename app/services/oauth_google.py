"""Google OAuth 2.0 (authorization code flow) — no extra deps.

Flow:
  1. /auth/google/login generates a CSRF `state`, stores it in a short-
     lived cookie, then 302s to Google's auth screen.
  2. User signs in on Google → Google redirects back to
     /auth/google/callback?code=...&state=... .
  3. Callback verifies `state` matches the cookie, POSTs the code to
     Google's token endpoint, then GETs userinfo to grab email/name/sub.
"""
from __future__ import annotations

import secrets
from typing import TypedDict

import httpx

from app.config import settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

OAUTH_STATE_COOKIE = "mb_oauth_state"
OAUTH_NEXT_COOKIE = "mb_oauth_next"


class GoogleUserInfo(TypedDict, total=False):
    sub: str
    email: str
    email_verified: bool
    name: str
    given_name: str
    family_name: str
    picture: str
    locale: str


def make_state() -> str:
    return secrets.token_urlsafe(24)


def authorize_url(state: str) -> str:
    from urllib.parse import urlencode
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        return r.json()


async def fetch_userinfo(access_token: str) -> GoogleUserInfo:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()
