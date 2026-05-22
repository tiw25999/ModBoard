from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


ROADMAP_STATUSES = ("planned", "in_progress", "done", "cancelled")


class RoadmapItem(Base):
    """A single item on a mod's public roadmap — fans can see what the
    author is planning, what's underway, and what shipped."""
    __tablename__ = "mod_roadmap_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mod_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mods.id", ondelete="CASCADE"), index=True
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="planned")
    # Lower position = higher in the list. Admin can drag/reorder.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    mod = relationship("Mod")
