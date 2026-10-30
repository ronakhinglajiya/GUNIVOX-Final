"""lead lock + timeline foundation (counsellor kanban + per-lead history)

Revision ID: 0004_lead_lock_and_timeline
Revises: 0003_user_last_seen
Create Date: 2026-06-05

Adds to `leads`:
  - locked_by_user_id   FK users(id) NULL  — who has the lead open
  - locked_until_at     DATETIME      NULL — when the lock auto-releases (UTC)
  - lock_extensions     INTEGER NOT NULL DEFAULT 0 — how many +5min extends used
  - assigned_counsellor_id FK users(id) NULL — Phase 2 routing; nullable today
  - lead_temperature    VARCHAR(10)   NULL — mirror of latest Call's classification
  - last_disposition    VARCHAR(40)   NULL — last manual outcome chosen
  - last_disposition_at DATETIME      NULL — when

Creates `lead_events` — append-only per-lead timeline. Every meaningful
action lands here (AI call, classification, lock, manual call, disposition,
admin reassign, etc.) so the lead detail page can show full history.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_lead_lock_and_timeline"
down_revision: Union[str, Sequence[str], None] = "0003_user_last_seen"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch_op:
        batch_op.add_column(sa.Column("locked_by_user_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("locked_until_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("lock_extensions", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("assigned_counsellor_id", sa.Integer(), nullable=True)
        )
        batch_op.add_column(sa.Column("lead_temperature", sa.String(10), nullable=True))
        batch_op.add_column(sa.Column("last_disposition", sa.String(40), nullable=True))
        batch_op.add_column(sa.Column("last_disposition_at", sa.DateTime(), nullable=True))
        batch_op.create_foreign_key(
            "fk_leads_locked_by",
            "users",
            ["locked_by_user_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_leads_assigned_counsellor",
            "users",
            ["assigned_counsellor_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_leads_locked_until_at", ["locked_until_at"], unique=False
        )
        batch_op.create_index(
            "ix_leads_temperature", ["lead_temperature"], unique=False
        )

    op.create_table(
        "lead_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("leads.id"), nullable=False, index=True),
        # 'system' / 'ai' / 'counsellor' / 'admin'
        sa.Column("actor_kind", sa.String(20), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        # see app.db.models.LeadEventType
        sa.Column("event_type", sa.String(40), nullable=False, index=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_lead_events_lead_created", "lead_events", ["lead_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_lead_events_lead_created", table_name="lead_events")
    op.drop_table("lead_events")
    with op.batch_alter_table("leads") as batch_op:
        batch_op.drop_index("ix_leads_temperature")
        batch_op.drop_index("ix_leads_locked_until_at")
        batch_op.drop_constraint("fk_leads_assigned_counsellor", type_="foreignkey")
        batch_op.drop_constraint("fk_leads_locked_by", type_="foreignkey")
        batch_op.drop_column("last_disposition_at")
        batch_op.drop_column("last_disposition")
        batch_op.drop_column("lead_temperature")
        batch_op.drop_column("assigned_counsellor_id")
        batch_op.drop_column("lock_extensions")
        batch_op.drop_column("locked_until_at")
        batch_op.drop_column("locked_by_user_id")
