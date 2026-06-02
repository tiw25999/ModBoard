"""mod_files table + source column + manual mod id sequence

Revision ID: a1b2c3d4e5f6
Revises: d4f7a2b1c80e
Create Date: 2026-06-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'd4f7a2b1c80e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New columns on mods. server_default backfills existing rows AND is
    # kept permanently so the rolling-deploy window (old + new code run
    # side by side for ~30s) stays safe: old code inserts a Mod without
    # the `source`/counter columns, and the DB default fills them in.
    # New code sets `source` explicitly via the ORM regardless.
    op.add_column('mods', sa.Column('source', sa.String(length=16),
                                    nullable=False, server_default='steam'))
    op.add_column('mods', sa.Column('game_name', sa.String(length=256), nullable=True))
    op.add_column('mods', sa.Column('view_count', sa.Integer(),
                                    nullable=False, server_default='0'))
    op.add_column('mods', sa.Column('download_count', sa.Integer(),
                                    nullable=False, server_default='0'))

    # Sequence for manual-mod ids. STARTs at 1; Steam workshop ids are
    # always >= 10^9, so small sequence values never collide.
    op.execute("CREATE SEQUENCE IF NOT EXISTS manual_mod_id_seq START WITH 1")

    op.create_table(
        'mod_files',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('mod_id', sa.BigInteger(), nullable=False),
        sa.Column('version', sa.String(length=64), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('stored_path', sa.Text(), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('content_type', sa.String(length=128), nullable=True),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('changelog', sa.Text(), nullable=True),
        sa.Column('download_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_current', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['mod_id'], ['mods.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_mod_files_mod_id'), 'mod_files', ['mod_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_mod_files_mod_id'), table_name='mod_files')
    op.drop_table('mod_files')
    op.execute("DROP SEQUENCE IF EXISTS manual_mod_id_seq")
    op.drop_column('mods', 'download_count')
    op.drop_column('mods', 'view_count')
    op.drop_column('mods', 'game_name')
    op.drop_column('mods', 'source')
