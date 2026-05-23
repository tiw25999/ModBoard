"""Admin API key minting + verification helpers."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.api_key import AdminApiKey

# Recognisable prefix so leaked tokens are obviously API keys in logs /
# scanners and can be regex-spotted in commit hooks.
KEY_PREFIX = "mbak_"
DEFAULT_TTL = timedelta(hours=24)


def hash_key(raw: str) -> str:
    """SHA-256 hex of the plain key. Fast (we hit it on every API
    request) and sufficient because the key itself carries 256 bits
    of entropy — no rainbow table feasible."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mint_key(ttl: timedelta = DEFAULT_TTL) -> tuple[str, str, str, datetime]:
    """Return `(plain_key, key_hash, prefix_for_display, expires_at)`.
    The caller is responsible for persisting hash + prefix + expiry —
    the plain key is shown to the human ONCE and never stored."""
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_key(raw), raw[:12], datetime.now(timezone.utc) + ttl


def _extract_token(request: Request) -> str:
    """Read the bearer token from `Authorization` or `X-API-Key`."""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.headers.get("x-api-key") or "").strip()


async def require_api_key(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> AdminApiKey:
    """FastAPI dependency: verify the bearer token resolves to a live
    AdminApiKey, bump its last_used_at, and return the row."""
    raw = _extract_token(request)
    if not raw or not raw.startswith(KEY_PREFIX):
        raise HTTPException(
            401,
            detail="API key required — send Authorization: Bearer mbak_... header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    h = hash_key(raw)
    now = datetime.now(timezone.utc)
    key = (await session.execute(
        select(AdminApiKey).where(
            AdminApiKey.key_hash == h,
            AdminApiKey.expires_at > now,
            AdminApiKey.revoked_at.is_(None),
        )
    )).scalar_one_or_none()
    if key is None:
        raise HTTPException(401, detail="Invalid, expired, or revoked API key")
    # Best-effort usage-tracking. Commit happens in the route's own
    # transaction; if the route raises we lose this bump but that's OK.
    key.last_used_at = now
    return key
