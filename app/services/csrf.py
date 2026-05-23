"""CSRF defence via the double-submit-cookie pattern.

Pattern:
  - On every request, a long-random token lives in a `mb_csrf` cookie
    (HttpOnly, SameSite=Strict). We mint it on first encounter and
    refresh the TTL on every GET response.
  - Every state-changing POST/PUT/DELETE/PATCH must include the same
    token either as a form field `_csrf` or as an `X-CSRF-Token`
    header. Middleware refuses requests that don't match.
  - templates read `request.state.csrf_token` and render it as a
    hidden input via the `csrf_input()` macro.

A few endpoints are exempt because they're entered cross-origin by
design (OAuth callback, Stripe webhooks if any):
  - /auth/google/callback  — Google's GET hop, no cookie loop possible
"""
from __future__ import annotations

import secrets

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import settings

CSRF_COOKIE = "mb_csrf"
CSRF_FIELD = "_csrf"
CSRF_HEADER = "x-csrf-token"
CSRF_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

# Path prefixes (or exact paths) that skip the CSRF check entirely.
# Keep this list tiny — every entry is an attack surface.
_EXEMPT_PATHS = (
    "/auth/google/callback",  # cross-origin GET from Google
)


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def get_or_create_csrf(request: Request) -> str:
    """Return the request's current CSRF token, minting one if absent."""
    existing = request.cookies.get(CSRF_COOKIE)
    if existing and len(existing) >= 32:
        return existing
    return _new_token()


def _is_exempt(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _EXEMPT_PATHS)


async def csrf_middleware(request: Request, call_next):
    """Validate CSRF on state-changing methods; refresh the cookie on
    every response so the TTL keeps rolling forward."""
    token = get_or_create_csrf(request)
    request.state.csrf_token = token

    method = request.method.upper()
    if method in ("POST", "PUT", "PATCH", "DELETE") and not _is_exempt(request.url.path):
        cookie_token = request.cookies.get(CSRF_COOKIE)
        if not cookie_token or len(cookie_token) < 32:
            return JSONResponse(
                {"detail": "CSRF cookie missing — refresh the page and try again."},
                status_code=403,
            )

        submitted: str | None = None
        ctype = request.headers.get("content-type", "").lower()
        if "form-urlencoded" in ctype or "multipart/form-data" in ctype:
            # Critical: buffer the body FIRST so `request.form()` parses
            # from the cache instead of consuming the receive() stream.
            # Without this, the downstream route handler sees an empty
            # body and every `Form(...)` parameter raises 422 — that
            # would brick all state-changing endpoints, including the
            # admin login form needed to recover.
            try:
                await request.body()
                form = await request.form()
                v = form.get(CSRF_FIELD)
                if isinstance(v, str):
                    submitted = v
            except Exception:
                submitted = None
        if not submitted:
            submitted = request.headers.get(CSRF_HEADER)

        if not submitted or not secrets.compare_digest(cookie_token, submitted):
            return JSONResponse(
                {"detail": "CSRF token mismatch — refresh the page and try again."},
                status_code=403,
            )

    response = await call_next(request)

    # (Re-)issue the cookie if missing or rotated. Strict SameSite is
    # safe here because the cookie is only consumed by same-origin
    # POSTs we generated ourselves.
    if request.cookies.get(CSRF_COOKIE) != token:
        response.set_cookie(
            CSRF_COOKIE,
            token,
            max_age=CSRF_MAX_AGE,
            httponly=True,
            samesite="strict",
            secure=settings.secure_cookies,
            path="/",
        )
    return response
