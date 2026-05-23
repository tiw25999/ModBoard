"""Security audit log.

Append-only record of sensitive actions and authentication outcomes.
The point isn't to drive UI — it's so that, after an incident, we can
answer "did anyone log in as admin on day X from IP Y" without grepping
ephemeral container stdout.

Keep the schema tight: no PII beyond actor_ip + actor_ua + (optional)
user_id; payload is short free-form text.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


# Known kinds — kept narrow so the audit UI (if/when we add one) can
# group + colour-code consistently.
AUDIT_KINDS = (
    "admin_login_success",
    "admin_login_fail",
    "admin_login_throttled",
    "oauth_login_success",
    "oauth_login_reject",       # email_verified=false, etc.
    "csrf_reject",              # set by middleware on token mismatch
    "admin_action",             # destructive admin POST (delete / pin / lock / ...)
    "account_delete",           # GDPR self-delete
    "account_export",           # GDPR self-export
)


class SecurityEvent(Base):
    __tablename__ = "security_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    actor_ip: Mapped[str | None] = mapped_column(String(64), index=True)
    actor_ua: Mapped[str | None] = mapped_column(String(256))
    target_path: Mapped[str | None] = mapped_column(String(256))

    # Set when the action was performed by (or against) a known user.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    # Short free-form detail string — e.g. the failed username,
    # the deleted-thread id, the admin route hit.
    detail: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
