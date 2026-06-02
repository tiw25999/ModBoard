from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text
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

    # Discriminates Steam-tracked mods from self-hosted manual entries.
    # Existing rows backfill to 'steam' in the migration.
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="steam")
    # Game grouping for manual mods (Steam mods use app_name instead).
    game_name: Mapped[str | None] = mapped_column(String(256))
    # Engagement counters for manual mods (Steam mods use snapshot data).
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    download_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

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
    files: Mapped[list["ModFile"]] = relationship(
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
    lifetime_favorited: Mapped[int | None] = mapped_column(Integer)
    followers: Mapped[int | None] = mapped_column(Integer)
    lifetime_followers: Mapped[int | None] = mapped_column(Integer)
    views: Mapped[int | None] = mapped_column(Integer)

    # vote_data block from IPublishedFileService/GetDetails
    votes_up: Mapped[int | None] = mapped_column(Integer)
    votes_down: Mapped[int | None] = mapped_column(Integer)
    vote_score: Mapped[float | None] = mapped_column(Float)

    # AJAX endpoint (Steam comment count from scrape) +
    # API-reported public comment count (more accurate when present).
    comments_count: Mapped[int | None] = mapped_column(Integer)
    num_comments_public: Mapped[int | None] = mapped_column(Integer)

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


class ModFile(Base):
    __tablename__ = "mod_files"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mod_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("mods.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128))
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    changelog: Mapped[str | None] = mapped_column(Text)
    download_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_current: Mapped[bool] = mapped_column(default=False, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    mod: Mapped[Mod] = relationship(back_populates="files")
