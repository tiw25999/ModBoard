from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    """Site user authenticated via Google OAuth.

    Anonymous posters keep their mb_token cookie. When an anon poster
    logs in, optional later work could backfill author_token → user_id
    on their past posts. For now, login starts a fresh identity.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Google's stable opaque user identifier (the "sub" claim)
    google_sub: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    email: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(128))
    avatar_url: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
