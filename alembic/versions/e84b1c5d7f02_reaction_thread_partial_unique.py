"""partial unique on forum_reactions for thread-level (post_id IS NULL)

Revision ID: e84b1c5d7f02
Revises: d92a8f3e1c40
Create Date: 2026-05-23 13:00:00.000000

Postgres treats NULL as distinct in unique constraints — so the
existing (post_id, voter_token, emoji) UNIQUE never fired for
thread-level reactions (post_id IS NULL). One voter could spam the
same emoji on a thread endlessly. This migration adds a partial
unique INDEX that catches the NULL case so the toggle becomes truly
idempotent.

Pre-flight cleanup: drop any pre-existing duplicates first, otherwise
the index creation fails.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "e84b1c5d7f02"
down_revision: Union[str, Sequence[str], None] = "d92a8f3e1c40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Remove existing duplicates for thread-level reactions (keep the
    # earliest row per group). Safe + idempotent.
    op.execute(
        """
        DELETE FROM forum_reactions a
        USING forum_reactions b
        WHERE a.post_id IS NULL
          AND b.post_id IS NULL
          AND a.thread_id = b.thread_id
          AND a.voter_token = b.voter_token
          AND a.emoji = b.emoji
          AND a.id > b.id;
        """
    )
    op.create_index(
        "uq_reaction_thread_voter_emoji",
        "forum_reactions",
        ["thread_id", "voter_token", "emoji"],
        unique=True,
        postgresql_where=op.f("post_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_reaction_thread_voter_emoji", table_name="forum_reactions")
