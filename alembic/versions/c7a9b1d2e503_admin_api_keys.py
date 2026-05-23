"""admin_api_keys table

Revision ID: c7a9b1d2e503
Revises: b3c5e8d9a710
Create Date: 2026-05-23 17:00:00.000000

Bearer-token auth for /api/admin/* scripted writes. 24h TTL by default,
revocable, sha256-hashed at rest.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7a9b1d2e503"
down_revision: Union[str, Sequence[str], None] = "b3c5e8d9a710"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_api_keys",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("key_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_admin_api_keys_key_hash", "admin_api_keys", ["key_hash"], unique=True)
    op.create_index("ix_admin_api_keys_expires_at", "admin_api_keys", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_admin_api_keys_expires_at", table_name="admin_api_keys")
    op.drop_index("ix_admin_api_keys_key_hash", table_name="admin_api_keys")
    op.drop_table("admin_api_keys")
