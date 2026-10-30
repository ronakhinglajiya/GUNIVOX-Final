"""funnel_stage + funnel_stage_set_at on leads

Revision ID: 0005_funnel_stage
Revises: 0004_lead_lock_and_timeline
Create Date: 2026-06-05

Phase 2 of the closing workflow. Adds a funnel-stage axis to leads:

  inquiry → engaged → applied → docs_received → enrolled
                            └→ lost

`status` (existing LeadStatus enum) stays as the dialer workflow state.
`funnel_stage` is the SALES funnel state — what stage of the admissions
process the lead is in. Set by the disposition mapping in inbox.unlock.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_funnel_stage"
down_revision: Union[str, Sequence[str], None] = "0004_lead_lock_and_timeline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch_op:
        batch_op.add_column(
            sa.Column(
                "funnel_stage",
                sa.String(20),
                nullable=False,
                server_default="inquiry",
            )
        )
        batch_op.add_column(
            sa.Column("funnel_stage_set_at", sa.DateTime(), nullable=True)
        )
        batch_op.create_index(
            "ix_leads_funnel_stage", ["funnel_stage"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("leads") as batch_op:
        batch_op.drop_index("ix_leads_funnel_stage")
        batch_op.drop_column("funnel_stage_set_at")
        batch_op.drop_column("funnel_stage")
