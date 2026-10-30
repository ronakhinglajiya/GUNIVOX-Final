"""add last_seen_at to users (admin team-activity view)

Revision ID: 0003_user_last_seen
Revises: 0002_lead_flow
Create Date: 2026-06-05

Adds a single column to the users table:
  - last_seen_at: timestamp updated by current_user() on each auth'd request
    (throttled to once per 60s per user in-process). Powers the admin
    Team page where the admin sees which counsellors are online / away /
    offline and what they did today.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_user_last_seen"
down_revision: Union[str, Sequence[str], None] = "0002_lead_flow"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("last_seen_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("last_seen_at")
