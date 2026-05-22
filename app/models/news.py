from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


NEWS_KINDS = ("info", "update", "warning", "celebration")


class NewsPost(Base):
    """Site-wide announcement / dev log entry. Admin-only authorship.
    The single newest active+banner entry shows as a site-wide banner;
    /news lists all (banner or not), newest first."""
    __tablename__ = "news_posts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    body_raw: Mapped[str] = mapped_column(Text, nullable=False)

    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # If True, this post also surfaces as the top banner until dismissed.
    show_banner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
