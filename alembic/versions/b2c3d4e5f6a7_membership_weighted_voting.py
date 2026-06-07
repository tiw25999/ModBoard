"""membership tiers + user_memberships + weighted forum votes

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-07 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None

_TIERS = [
    # (name, price_usd_cents, vote_weight, emoji)
    ("Coffee",   300,  2, "☕"),
    ("Snack",    500,  3, "\U0001f36a"),
    ("Lunch",   1000,  5, "\U0001f371"),
    ("Big Tip", 2500, 10, "⭐"),
    ("Sponsor", 5000, 20, "\U0001f31f"),
]


def upgrade() -> None:
    op.create_table(
        "membership_tiers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("price_usd_cents", sa.Integer(), nullable=False),
        sa.Column("vote_weight", sa.Integer(), nullable=False),
        sa.Column("emoji", sa.String(length=8), nullable=False),
        sa.Column("stripe_price_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.bulk_insert(
        sa.table(
            "membership_tiers",
            sa.column("name", sa.String),
            sa.column("price_usd_cents", sa.Integer),
            sa.column("vote_weight", sa.Integer),
            sa.column("emoji", sa.String),
            sa.column("stripe_price_id", sa.String),
            sa.column("active", sa.Boolean),
        ),
        [
            {"name": n, "price_usd_cents": p, "vote_weight": w,
             "emoji": e, "stripe_price_id": "", "active": True}
            for n, p, w, e in _TIERS
        ],
    )

    op.create_table(
        "user_memberships",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tier_id", sa.Integer(), nullable=False),
        sa.Column("stripe_checkout_session_id", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tier_id"], ["membership_tiers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_checkout_session_id", name="uq_membership_session"),
    )
    op.create_index("ix_user_memberships_user_id", "user_memberships", ["user_id"])

    # Add new columns to forum_upvotes (nullable so existing rows survive).
    op.add_column("forum_upvotes",
        sa.Column("voter_user_id", sa.Integer(), nullable=True))
    op.add_column("forum_upvotes",
        sa.Column("vote_weight", sa.Integer(), nullable=False, server_default="1"))
    op.create_foreign_key(
        "fk_forum_upvotes_voter_user",
        "forum_upvotes", "users", ["voter_user_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_forum_upvotes_voter_user_id", "forum_upvotes", ["voter_user_id"])
    op.create_unique_constraint(
        "uq_upvote_thread_user", "forum_upvotes", ["thread_id", "voter_user_id"]
    )

    # Rename upvotes -> vote_score; copy existing values.
    op.add_column("forum_threads",
        sa.Column("vote_score", sa.Integer(), nullable=False, server_default="0"))
    op.execute("UPDATE forum_threads SET vote_score = upvotes")
    op.drop_column("forum_threads", "upvotes")


def downgrade() -> None:
    op.add_column("forum_threads",
        sa.Column("upvotes", sa.Integer(), nullable=False, server_default="0"))
    op.execute("UPDATE forum_threads SET upvotes = vote_score")
    op.drop_column("forum_threads", "vote_score")

    op.drop_constraint("uq_upvote_thread_user", "forum_upvotes", type_="unique")
    op.drop_index("ix_forum_upvotes_voter_user_id", table_name="forum_upvotes")
    # Drop FK before dropping the column — PostgreSQL refuses to drop a column
    # that is still referenced by a foreign key constraint.
    op.drop_constraint("fk_forum_upvotes_voter_user", "forum_upvotes", type_="foreignkey")
    op.drop_column("forum_upvotes", "vote_weight")
    op.drop_column("forum_upvotes", "voter_user_id")

    op.drop_index("ix_user_memberships_user_id", table_name="user_memberships")
    op.drop_table("user_memberships")
    op.drop_table("membership_tiers")
