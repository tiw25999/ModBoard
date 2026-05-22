from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Mod(Base):
    __tablename__ = "mods"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Steam workshop file id
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    workshop_url: Mapped[str | None] = mapped_column(Text)
    github_url: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    public: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Parent Steam app the mod belongs to — populated from the API.
    # Both null until the first poll sees the mod.
    app_id: Mapped[int | None] = mapped_column(Integer, index=True)
    app_name: Mapped[str | None] = mapped_column(String(256))

    snapshots: Mapped[list["ModSnapshot"]] = relationship(
        back_populates="mod", cascade="all, delete-orphan"
    )
    comments: Mapped[list["ModComment"]] = relationship(
        back_populates="mod", cascade="all, delete-orphan"
    )
    changelogs: Mapped[list["ModChangelog"]] = relationship(
        back_populates="mod", cascade="all, delete-orphan"
    )
    discussions: Mapped[list["ModDiscussion"]] = relationship(
        back_populates="mod", cascade="all, delete-orphan"
    )


class ModSnapshot(Base):
    __tablename__ = "mod_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mod_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("mods.id", ondelete="CASCADE"))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # raw API fields (names mirror Steam's response; misleading vs displayed labels)
    subscriptions: Mapped[int | None] = mapped_column(Integer)
    lifetime_subs: Mapped[int | None] = mapped_column(Integer)
    favorited: Mapped[int | None] = mapped_column(Integer)
    views: Mapped[int | None] = mapped_column(Integer)

    # AJAX endpoint
    comments_count: Mapped[int | None] = mapped_column(Integer)

    # HTML-scraped values — canonical for display, match what users see on Steam
    visitors_display: Mapped[int | None] = mapped_column(Integer)
    subscribers_display: Mapped[int | None] = mapped_column(Integer)
    favorites_display: Mapped[int | None] = mapped_column(Integer)

    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    mod: Mapped[Mod] = relationship(back_populates="snapshots")


class ModComment(Base):
    __tablename__ = "mod_comments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mod_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mods.id", ondelete="CASCADE"), index=True
    )
    comment_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)

    author_name: Mapped[str | None] = mapped_column(String(128))
    author_profile_url: Mapped[str | None] = mapped_column(Text)
    author_avatar_url: Mapped[str | None] = mapped_column(Text)

    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    mod: Mapped[Mod] = relationship(back_populates="comments")


class ModChangelog(Base):
    __tablename__ = "mod_changelogs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mod_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mods.id", ondelete="CASCADE"), index=True
    )
    post_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)

    headline: Mapped[str | None] = mapped_column(String(256))
    author_name: Mapped[str | None] = mapped_column(String(128))
    author_profile_url: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    mod: Mapped[Mod] = relationship(back_populates="changelogs")


class ModDiscussion(Base):
    __tablename__ = "mod_discussions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mod_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mods.id", ondelete="CASCADE"), index=True
    )
    thread_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    body_preview: Mapped[str | None] = mapped_column(Text)
    author_name: Mapped[str | None] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(Text)

    reply_count: Mapped[int | None] = mapped_column(Integer)
    last_post_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_post_author: Mapped[str | None] = mapped_column(String(128))

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    mod: Mapped[Mod] = relationship(back_populates="discussions")
