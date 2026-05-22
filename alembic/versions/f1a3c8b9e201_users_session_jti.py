"""users.session_jti for session rotation + revocation

Revision ID: f1a3c8b9e201
Revises: e84b1c5d7f02
Create Date: 2026-05-23 14:00:00.000000

Adds a per-user random `session_jti` that the signed session cookie
embeds. Middleware rejects any cookie whose jti doesn't match the
current row — gives us session-fixation defence + server-side logout
revocation.

The session cookie salt is bumped from v1 → v2 at the same time, so
all existing v1 cookies become invalid and users re-login once. Their
fresh login mints a jti. Additive nullable column → no downtime risk.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1a3c8b9e201"
down_revision: Union[str, Sequence[str], None] = "e84b1c5d7f02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("session_jti", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "session_jti")
