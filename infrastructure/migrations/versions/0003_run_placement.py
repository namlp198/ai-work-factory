"""run placement: one machine per workflow run (ADR-008)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-19 09:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

JsonType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # Existing rows get "not placed" values: old runs and their jobs keep the any-matching-worker behaviour.
    with op.batch_alter_table('workflow_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('assigned_worker_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('assigned_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('requirements', JsonType, nullable=False, server_default='{}'))
        batch_op.create_index(batch_op.f('ix_workflow_runs_assigned_worker_id'), ['assigned_worker_id'], unique=False)

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pinned', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('assigned_worker_id', sa.String(length=36), nullable=True))
        batch_op.create_index(batch_op.f('ix_jobs_assigned_worker_id'), ['assigned_worker_id'], unique=False)

    with op.batch_alter_table('workers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('max_jobs', sa.Integer(), nullable=False, server_default='1'))


def downgrade() -> None:
    with op.batch_alter_table('workers', schema=None) as batch_op:
        batch_op.drop_column('max_jobs')

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_jobs_assigned_worker_id'))
        batch_op.drop_column('assigned_worker_id')
        batch_op.drop_column('pinned')

    with op.batch_alter_table('workflow_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_workflow_runs_assigned_worker_id'))
        batch_op.drop_column('requirements')
        batch_op.drop_column('assigned_at')
        batch_op.drop_column('assigned_worker_id')
