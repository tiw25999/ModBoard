from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ModSubscription(Base):
    """One row per (user, mod) follow. Followers get notified on new
    comments, releases, and discussion threads scraped by the poller."""
    __tablename__ = "mod_subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "mod_id", name="uq_modsub_user_mod"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    mod_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mods.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
