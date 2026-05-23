"""Helper for writing rows into security_events without ceremony.

Usage:
    from app.services.audit import log_event
    await log_event(session, "admin_login_fail", request, detail="user=x")
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import SecurityEvent
from app.services.ratelimit import client_ip


async def log_event(
    session: AsyncSession,
    kind: str,
    request: Request | None,
    *,
    user_id: int | None = None,
    detail: str | None = None,
) -> None:
    """Append a security_events row. Best-effort: failures here MUST
    NOT propagate up and break the originating user action, so callers
    can rely on this without try/except."""
    try:
        ip = client_ip(request) if request else None
        ua = (request.headers.get("user-agent") if request else None) or None
        path = (request.url.path if request else None)
        session.add(SecurityEvent(
            kind=kind[:32],
            actor_ip=(ip or None) if ip != "unknown" else None,
            actor_ua=(ua[:256] if ua else None),
            target_path=(path[:256] if path else None),
            user_id=user_id,
            detail=(detail[:2000] if detail else None),
            created_at=datetime.now(timezone.utc),
        ))
        # NOTE: we do NOT commit here. The caller's transaction will
        # commit (or not) — if their action rolls back, so does the
        # audit row, which is the right semantic for "successful
        # action got logged + failed action didn't".
    except Exception:
        # If audit logging itself fails (DB issue?), don't take down
        # the originating request.
        pass
