"""security_events audit log table

Revision ID: b3c5e8d9a710
Revises: a5e1d2c0f3a4
Create Date: 2026-05-23 16:00:00.000000

Append-only audit log for: admin login attempts (success / fail /
throttled), OAuth login outcomes, CSRF rejections, admin destructive
actions, GDPR self-delete / self-export.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3c5e8d9a710"
down_revision: Union[str, Sequence[str], None] = "a5e1d2c0f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "security_events",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("actor_ip", sa.String(length=64), nullable=True),
        sa.Column("actor_ua", sa.String(length=256), nullable=True),
        sa.Column("target_path", sa.String(length=256), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_security_events_kind", "security_events", ["kind"])
    op.create_index("ix_security_events_actor_ip", "security_events", ["actor_ip"])
    op.create_index("ix_security_events_user_id", "security_events", ["user_id"])
    op.create_index("ix_security_events_created_at", "security_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_security_events_created_at", table_name="security_events")
    op.drop_index("ix_security_events_user_id", table_name="security_events")
    op.drop_index("ix_security_events_actor_ip", table_name="security_events")
    op.drop_index("ix_security_events_kind", table_name="security_events")
    op.drop_table("security_events")
