"""Admin API keys — short-lived bearer tokens for scripted writes.

Auth model:
  - Admin generates a key in the admin UI; the plain token is shown
    ONCE on creation. Only its SHA-256 hash + a non-secret prefix are
    persisted. Loss of the plain key means revoke + re-issue.
  - Keys have a hard expiry (24h by default) and can be revoked at
    any time. The auth dep rejects expired or revoked keys.
  - Used only for /api/admin/* endpoints — the admin UI itself still
    requires the session cookie + CSRF token.
"""
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AdminApiKey(Base):
    __tablename__ = "admin_api_keys"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # SHA-256 hex of the plain key (64 chars). Random 256-bit tokens
    # don't need a slow KDF — brute force is infeasible.
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # First few chars of the plain key (e.g. "mbak_xyz123") so the UI
    # can show a recognisable handle without storing the secret.
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    # Free-form human label — "import-script", "n8n-staging", etc.
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
