"""widen users.session_jti to VARCHAR(64) defensively

Revision ID: a5e1d2c0f3a4
Revises: f1a3c8b9e201
Create Date: 2026-05-23 15:00:00.000000

Current jti is `secrets.token_urlsafe(16)` → 22 chars, fits in
VARCHAR(32). Widening to 64 future-proofs us against bumping to
token_urlsafe(32) (44 chars) for stronger entropy without silently
truncating and bricking every login. Cheap online ALTER.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a5e1d2c0f3a4"
down_revision: Union[str, Sequence[str], None] = "f1a3c8b9e201"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "users", "session_jti",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "users", "session_jti",
        existing_type=sa.String(length=64),
        type_=sa.String(length=32),
        existing_nullable=True,
    )
