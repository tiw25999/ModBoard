from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


# Notification kinds — keep narrow so the UI can render each type with a
# specific verb + icon.
NOTIFICATION_KINDS = (
    "thread_reply",       # someone replied to your thread
    "thread_status",      # an admin changed your thread's status
    "thread_locked",      # an admin locked your thread
    "reply_to_reply",     # someone replied after you replied in a thread
    "mention",            # @-mentioned in a forum post
    "mod_new_comment",    # a mod you follow got a new Steam comment
    "mod_new_changelog",  # a mod you follow shipped a release note
    "mod_new_discussion", # a mod you follow has a new Steam discussion thread
)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)

    # Free-form references resolved by kind:
    #   thread_reply  → ref_thread_id, ref_post_id, actor_name
    #   thread_status → ref_thread_id, payload = new status
    thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("forum_threads.id", ondelete="CASCADE"), index=True
    )
    post_id: Mapped[int | None] = mapped_column(
        ForeignKey("forum_posts.id", ondelete="CASCADE")
    )
    mod_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("mods.id", ondelete="CASCADE"), index=True
    )
    actor_name: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[str | None] = mapped_column(Text)

    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
