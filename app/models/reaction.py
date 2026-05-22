from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


# The fixed palette — keep small to avoid emoji-pageant bloat.
# Order matters: it's the order they render in the picker.
REACTION_EMOJIS = ("👍", "❤️", "🎉", "🙏", "🤔", "😂")


class ForumReaction(Base):
    """One reaction per (post, voter_token, emoji). Toggling re-clicks the
    same emoji to remove. A voter can react with multiple different emojis
    on the same post."""
    __tablename__ = "forum_reactions"
    __table_args__ = (
        UniqueConstraint("post_id", "voter_token", "emoji", name="uq_reaction_post_voter_emoji"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Either thread_id (top-level post) or post_id (reply). Exactly one of
    # them is set; we use post_id with NULL meaning "the original thread".
    thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("forum_threads.id", ondelete="CASCADE"), index=True
    )
    post_id: Mapped[int | None] = mapped_column(
        ForeignKey("forum_posts.id", ondelete="CASCADE"), index=True
    )

    voter_token: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    emoji: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
