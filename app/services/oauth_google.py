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

import base64
import hashlib
import secrets
from typing import TypedDict

import httpx

from app.config import settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

OAUTH_STATE_COOKIE = "mb_oauth_state"
OAUTH_NEXT_COOKIE = "mb_oauth_next"
OAUTH_PKCE_COOKIE = "mb_oauth_pkce"


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


def make_pkce_verifier() -> str:
    """PKCE code_verifier — 64 bytes urlsafe, exceeds RFC 7636 spec
    minimum of 43 chars and well under the 128 cap."""
    return secrets.token_urlsafe(64)


def pkce_challenge(verifier: str) -> str:
    """S256 code_challenge: base64url(sha256(verifier)), no padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorize_url(state: str, code_challenge: str | None = None) -> str:
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
    if code_challenge:
        # PKCE — even though we're a confidential client with a
        # client_secret, PKCE shuts down the authorization-code-
        # interception family of attacks (malicious browser
        # extension reading the redirect, etc.) for free.
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    return f"{AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str, code_verifier: str | None = None) -> dict:
    data: dict[str, str] = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": settings.google_redirect_uri,
        "grant_type": "authorization_code",
    }
    if code_verifier:
        data["code_verifier"] = code_verifier
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            TOKEN_URL,
            data=data,
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
