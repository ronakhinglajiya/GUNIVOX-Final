"""add lead-flow columns to calls

Revision ID: 0002_lead_flow
Revises: 0001_initial
Create Date: 2026-05-18

Adds four columns to the calls table:
  - lead_temperature: 'hot' | 'warm' | 'cold' from the post-call classifier
  - lead_program: the program the caller showed primary interest in
  - lead_reasons: short LLM explanation of the temperature pick
  - caller_name: the name extracted on turn 1 (separate from outcome/summary)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_lead_flow"
down_revision: Union[str, Sequence[str], None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("calls") as batch_op:
        batch_op.add_column(sa.Column("lead_temperature", sa.String(10), nullable=True))
        batch_op.add_column(sa.Column("lead_program", sa.String(120), nullable=True))
        batch_op.add_column(sa.Column("lead_reasons", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("caller_name", sa.String(120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("calls") as batch_op:
        batch_op.drop_column("caller_name")
        batch_op.drop_column("lead_reasons")
        batch_op.drop_column("lead_program")
        batch_op.drop_column("lead_temperature")
