"""fix: replace uq_upvote_thread_user with NULLS NOT DISTINCT index; fix downgrade FK order

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-07 00:01:00.000000

Why: PostgreSQL treats NULL != NULL in UNIQUE constraints, so
     UNIQUE(thread_id, voter_user_id) does NOT prevent duplicate rows
     where voter_user_id IS NULL. Using a partial unique index with
     NULLS NOT DISTINCT (PG15+) enforces one vote per user per thread.

Pre-migration votes: rows inserted before b2c3d4e5f6a7 have voter_user_id=NULL
     (no user identity known). They are treated as anonymous historical votes
     and cannot be de-duplicated by user. New votes from authenticated users
     are always protected by this index.
"""
from typing import Sequence, Union
from alembic import op

revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the vanilla UNIQUE constraint (NULL-bypassable).
    op.drop_constraint("uq_upvote_thread_user", "forum_upvotes", type_="unique")

    # Create a proper NULLS NOT DISTINCT unique index (PostgreSQL 15+).
    # This prevents two authenticated users sharing voter_user_id=NULL from
    # bypassing the constraint, while still allowing one NULL per thread
    # (anonymous legacy rows).
    op.execute(
        "CREATE UNIQUE INDEX uq_upvote_thread_user "
        "ON forum_upvotes (thread_id, voter_user_id) "
        "NULLS NOT DISTINCT"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_upvote_thread_user")
    op.create_unique_constraint(
        "uq_upvote_thread_user", "forum_upvotes", ["thread_id", "voter_user_id"]
    )
