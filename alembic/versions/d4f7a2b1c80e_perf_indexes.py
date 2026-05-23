"""perf indexes — composite indexes on hot query paths

Revision ID: d4f7a2b1c80e
Revises: c7a9b1d2e503
Create Date: 2026-05-23 18:00:00.000000

Targeted indexes for queries that show up on every page:

  - mod_snapshots(mod_id, captured_at DESC)
      Powers _latest_snapshots_by_mod (DISTINCT ON) on the homepage.
      Without this, Postgres has to scan + sort every snapshot per mod.

  - notifications(user_id, read_at)
      Powers the unread-count query in attach_current_user middleware,
      which runs on EVERY request from a logged-in user.

  - forum_threads(last_post_at DESC)
      Powers the forum list + sitemap. Already indexed on pinned but
      not on the ORDER BY column.

  - forum_posts(thread_id, created_at)
      Powers the thread view + reply_count derivations.

All CONCURRENT-safe additions, no table rewrites.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "d4f7a2b1c80e"
down_revision: Union[str, Sequence[str], None] = "c7a9b1d2e503"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_mod_snapshots_mod_captured",
        "mod_snapshots",
        ["mod_id", op.f("captured_at")],
        postgresql_using="btree",
    )
    op.create_index(
        "ix_notifications_user_read",
        "notifications",
        ["user_id", "read_at"],
    )
    op.create_index(
        "ix_forum_threads_last_post_at",
        "forum_threads",
        [op.f("last_post_at")],
    )
    op.create_index(
        "ix_forum_posts_thread_created",
        "forum_posts",
        ["thread_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_forum_posts_thread_created", table_name="forum_posts")
    op.drop_index("ix_forum_threads_last_post_at", table_name="forum_threads")
    op.drop_index("ix_notifications_user_read", table_name="notifications")
    op.drop_index("ix_mod_snapshots_mod_captured", table_name="mod_snapshots")
