from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.mod import Mod


# Tag = single category per thread. Steam-style triage.
THREAD_KINDS = ("discussion", "bug", "request", "question")
THREAD_STATUSES = ("open", "addressed", "wontfix", "closed")


class ForumThread(Base):
    __tablename__ = "forum_threads"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    body_raw: Mapped[str] = mapped_column(Text, nullable=False)

    # Optional link to a tracked mod. Nullable so threads can be off-topic /
    # cross-mod (e.g. "general support", "site feedback").
    mod_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("mods.id", ondelete="SET NULL"), index=True
    )

    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="discussion")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")

    author_name: Mapped[str] = mapped_column(String(64), nullable=False)
    author_token: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Set when an authenticated user posts; null for anon posts.
    author_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    upvotes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reply_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_post_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    mod: Mapped["Mod | None"] = relationship()
    posts: Mapped[list["ForumPost"]] = relationship(
        back_populates="thread", cascade="all, delete-orphan"
    )
    upvote_records: Mapped[list["ForumUpvote"]] = relationship(
        back_populates="thread", cascade="all, delete-orphan"
    )


class ForumPost(Base):
    __tablename__ = "forum_posts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("forum_threads.id", ondelete="CASCADE"), index=True
    )
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    body_raw: Mapped[str] = mapped_column(Text, nullable=False)

    author_name: Mapped[str] = mapped_column(String(64), nullable=False)
    author_token: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    author_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    thread: Mapped[ForumThread] = relationship(back_populates="posts")


class ForumUpvote(Base):
    __tablename__ = "forum_upvotes"
    __table_args__ = (UniqueConstraint("thread_id", "voter_token", name="uq_upvote_thread_voter"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("forum_threads.id", ondelete="CASCADE"), index=True
    )
    voter_token: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    thread: Mapped[ForumThread] = relationship(back_populates="upvote_records")
