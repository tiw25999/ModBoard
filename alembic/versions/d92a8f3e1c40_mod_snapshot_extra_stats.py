"""mod_snapshot extra stats from IPublishedFileService/GetDetails

Revision ID: d92a8f3e1c40
Revises: c75b72ec2554
Create Date: 2026-05-23 12:00:00.000000

Adds the engagement counters returned by the modern
IPublishedFileService/GetDetails endpoint that the legacy
ISteamRemoteStorage one didn't expose: vote_data, followers,
lifetime_favorited, and num_comments_public.

All columns are nullable + additive — backward-compatible during
rolling deploy.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d92a8f3e1c40"
down_revision: Union[str, Sequence[str], None] = "c75b72ec2554"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("mod_snapshots", sa.Column("lifetime_favorited", sa.Integer(), nullable=True))
    op.add_column("mod_snapshots", sa.Column("followers", sa.Integer(), nullable=True))
    op.add_column("mod_snapshots", sa.Column("lifetime_followers", sa.Integer(), nullable=True))
    op.add_column("mod_snapshots", sa.Column("votes_up", sa.Integer(), nullable=True))
    op.add_column("mod_snapshots", sa.Column("votes_down", sa.Integer(), nullable=True))
    op.add_column("mod_snapshots", sa.Column("vote_score", sa.Float(), nullable=True))
    op.add_column("mod_snapshots", sa.Column("num_comments_public", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("mod_snapshots", "num_comments_public")
    op.drop_column("mod_snapshots", "vote_score")
    op.drop_column("mod_snapshots", "votes_down")
    op.drop_column("mod_snapshots", "votes_up")
    op.drop_column("mod_snapshots", "lifetime_followers")
    op.drop_column("mod_snapshots", "followers")
    op.drop_column("mod_snapshots", "lifetime_favorited")
