from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class UserRole(str, Enum):
    ADMIN = "admin"
    COUNSELLOR = "counsellor"


class LeadStatus(str, Enum):
    NEW = "new"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    CONTACTED = "contacted"
    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    CALLBACK_SCHEDULED = "callback_scheduled"
    NO_ANSWER = "no_answer"
    DND = "dnd"
    FAILED = "failed"


class CallOutcome(str, Enum):
    COMPLETED = "completed"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    FAILED = "failed"
    TRANSFERRED = "transferred"
    CALLBACK_SCHEDULED = "callback_scheduled"


class CampaignStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"


class TurnRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class LeadEventActorKind(str, Enum):
    SYSTEM = "system"        # background sweep, classifier, dialer
    AI = "ai"                # AI-driven action attached to a Call
    COUNSELLOR = "counsellor"
    ADMIN = "admin"


class LeadEventType(str, Enum):
    # Auto / AI-driven
    AI_CALL_STARTED = "ai_call_started"
    AI_CALL_ENDED = "ai_call_ended"
    TEMPERATURE_SET = "temperature_set"           # classifier wrote hot/warm/cold
    # Counsellor-driven (kanban + manual dialer)
    LOCK_ACQUIRED = "lock_acquired"
    LOCK_EXTENDED = "lock_extended"
    LOCK_EXPIRED = "lock_expired"                 # auto-released without disposition
    LOCK_RELEASED = "lock_released"               # explicit release w/ disposition + notes
    MANUAL_CALL_PLACED = "manual_call_placed"
    DISPOSITION_SET = "disposition_set"
    NOTES_UPDATED = "notes_updated"
    CALLBACK_SCHEDULED = "callback_scheduled"
    FUNNEL_STAGE_CHANGED = "funnel_stage_changed"  # Phase 2
    DND_SET = "dnd_set"                            # Phase 2 — added to DNC list
    # Admin-driven
    REASSIGNED = "reassigned"
    TEMPERATURE_OVERRIDDEN = "temperature_overridden"  # Phase 4


class FunnelStage(str, Enum):
    """The admissions sales-funnel state. Independent of `LeadStatus`
    (which tracks the dialer workflow). Set by the disposition mapping
    in inbox.unlock; never advanced by the AI alone."""
    INQUIRY = "inquiry"                # cold contact — no real conversation yet
    ENGAGED = "engaged"                # had a real conversation, considering
    APPLIED = "applied"                # application form started or submitted
    DOCS_RECEIVED = "docs_received"    # documents in for review
    ENROLLED = "enrolled"              # fees paid, seat confirmed
    LOST = "lost"                      # walked away (any not-interested or already-enrolled-elsewhere)


# Disposition codes returned from POST /api/leads/{id}/unlock. These map
# 1:1 to outcome categories the Reports page aggregates. Keep the
# string values stable — they're persisted in `lead.last_disposition`
# and `lead_events.payload`.
class Disposition(str, Enum):
    APPLICATION_STARTED = "application_started"
    WANTS_BROCHURE = "wants_brochure"
    CALLBACK_REQUESTED = "callback_requested"
    INTERESTED_NURTURE = "interested_nurture"
    NOT_INTERESTED_FEE = "not_interested_fee"
    NOT_INTERESTED_DISTANCE = "not_interested_distance"
    NOT_INTERESTED_OTHER = "not_interested_other"
    ALREADY_ENROLLED_ELSEWHERE = "already_enrolled_elsewhere"
    WRONG_NUMBER = "wrong_number"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    DO_NOT_CALL = "do_not_call"


# Dispositions where notes aren't required to release the lock —
# the counsellor literally couldn't have a conversation.
NOTES_OPTIONAL_DISPOSITIONS = {
    Disposition.NO_ANSWER.value,
    Disposition.BUSY.value,
    Disposition.WRONG_NUMBER.value,
}


# The 2-option shortlist actually shown in the Inbox modal
# (simplification 2026-06-12). The full `Disposition` enum + `DISPOSITION_MAPPING`
# stay intact so historical events and the wider API remain valid — this
# constant just narrows what the Inbox UI displays.
#
#   INTERESTED_NURTURE  → real interest, will need a follow-up call.
#                         Optional callback datetime supported.
#   NOT_INTERESTED_OTHER → lost. Funnel state goes to lost.
INBOX_DISPOSITIONS: list[str] = [
    Disposition.INTERESTED_NURTURE.value,
    Disposition.NOT_INTERESTED_OTHER.value,
]


# ─────────────────── Disposition → Lead state mapping (Phase 2) ──────────
# Single source of truth for "what does picking this outcome DO to the lead?".
# Three axes get written on unlock:
#   - funnel_stage  : sales-funnel position (see FunnelStage)
#   - status        : workflow status (see LeadStatus) — keeps the existing
#                     pipeline page + reports queries working
#   - set_dnd       : if True, lead.dnd=True (adds to DNC list)
# `None` for funnel_stage means "don't touch the existing stage" — used for
# unable-to-reach dispositions where the lead's funnel position hasn't
# actually changed.
DISPOSITION_MAPPING: dict[str, tuple[FunnelStage | None, str, bool]] = {
    # ── Active funnel: counsellor had a real conversation ──
    Disposition.APPLICATION_STARTED.value:
        (FunnelStage.APPLIED,   LeadStatus.INTERESTED.value,         False),
    Disposition.WANTS_BROCHURE.value:
        (FunnelStage.ENGAGED,   LeadStatus.INTERESTED.value,         False),
    Disposition.CALLBACK_REQUESTED.value:
        (FunnelStage.ENGAGED,   LeadStatus.CALLBACK_SCHEDULED.value, False),
    Disposition.INTERESTED_NURTURE.value:
        (FunnelStage.ENGAGED,   LeadStatus.INTERESTED.value,         False),

    # ── Lost: counsellor confirmed not pursuing ──
    Disposition.NOT_INTERESTED_FEE.value:
        (FunnelStage.LOST,      LeadStatus.NOT_INTERESTED.value,     False),
    Disposition.NOT_INTERESTED_DISTANCE.value:
        (FunnelStage.LOST,      LeadStatus.NOT_INTERESTED.value,     False),
    Disposition.NOT_INTERESTED_OTHER.value:
        (FunnelStage.LOST,      LeadStatus.NOT_INTERESTED.value,     False),
    Disposition.ALREADY_ENROLLED_ELSEWHERE.value:
        (FunnelStage.LOST,      LeadStatus.NOT_INTERESTED.value,     False),

    # ── DNC: actively remove from future dialing ──
    Disposition.WRONG_NUMBER.value:
        (FunnelStage.LOST,      LeadStatus.FAILED.value,             True),
    Disposition.DO_NOT_CALL.value:
        (FunnelStage.LOST,      LeadStatus.DND.value,                True),

    # ── Couldn't reach: funnel_stage UNCHANGED (lead hasn't actually advanced) ──
    Disposition.NO_ANSWER.value:
        (None,                  LeadStatus.NO_ANSWER.value,          False),
    Disposition.BUSY.value:
        (None,                  LeadStatus.CONTACTED.value,          False),
}


# Dispositions that mark the lead as "closing" — i.e. in the
# active-but-late-stage admissions pipeline. Surfaces on the Closures page.
CLOSING_FUNNEL_STAGES = {
    FunnelStage.APPLIED.value,
    FunnelStage.DOCS_RECEIVED.value,
    FunnelStage.ENROLLED.value,
}


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=UserRole.COUNSELLOR.value)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Updated by current_user() (throttled to once per 60s per user, in-process)
    # so the admin Team page can show online / away / offline presence.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Lead(Base, TimestampMixin):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    program_interest: Mapped[str | None] = mapped_column(String(255), nullable=True)
    preferred_language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=LeadStatus.NEW.value)
    dnd: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Kanban / lock fields (Phase 1, 2026-06-05).
    # Counsellor "claims" a hot lead by acquiring the lock. While held,
    # other counsellors see the card greyed-out. Released by:
    #   - explicit unlock with disposition + notes  → LOCK_RELEASED event
    #   - 5 min (+ up to 2 × 5 min extends) timeout → LOCK_EXPIRED event
    locked_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    locked_until_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    lock_extensions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Routing target (Phase 2). Nullable today — Phase 1 uses
    # first-to-lock semantics regardless of who's assigned.
    assigned_counsellor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    # Mirror of the latest Call.lead_temperature so the Inbox can
    # filter without joining calls. Updated by bridge.finalize_call.
    lead_temperature: Mapped[str | None] = mapped_column(
        String(10), nullable=True, index=True
    )

    # Last manual outcome the counsellor chose on unlock.
    last_disposition: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_disposition_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Phase 2 (2026-06-05): admissions sales-funnel stage. Independent of
    # `status` (which is the dialer workflow state). Default 'inquiry' for
    # every new lead; advanced by disposition mapping in inbox.unlock.
    funnel_stage: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FunnelStage.INQUIRY.value, index=True
    )
    funnel_stage_set_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("phone", name="uq_leads_phone"),)


class LeadEvent(Base):
    """Append-only per-lead timeline. Every action lands here in order.

    No TimestampMixin — we control created_at server-side. Never updated
    or deleted; if something is wrong, append a corrective event.
    """

    __tablename__ = "lead_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("leads.id"), nullable=False, index=True
    )
    actor_kind: Mapped[str] = mapped_column(String(20), nullable=False)  # LeadEventActorKind
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )


class Campaign(Base, TimestampMixin):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=CampaignStatus.DRAFT.value)
    working_hours: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    retry_policy: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    script_template: Mapped[str | None] = mapped_column(Text, nullable=True)


class CampaignLead(Base, TimestampMixin):
    __tablename__ = "campaign_leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), nullable=False, index=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=LeadStatus.QUEUED.value)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (UniqueConstraint("campaign_id", "lead_id", name="uq_campaign_lead"),)


class Call(Base, TimestampMixin):
    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), nullable=False, index=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True, index=True)
    assigned_counsellor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    vobiz_call_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)

    language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(30), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    recording_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ttfa_p50_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Lead-flow classifier output, populated at hangup. NULL if classifier
    # didn't run (e.g. zero-turn call) or failed.
    lead_temperature: Mapped[str | None] = mapped_column(String(10), nullable=True)  # 'hot' / 'warm' / 'cold'
    lead_program: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lead_reasons: Mapped[str | None] = mapped_column(Text, nullable=True)
    caller_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    turns: Mapped[list[Turn]] = relationship(back_populates="call", cascade="all, delete-orphan")


class Turn(Base, TimestampMixin):
    __tablename__ = "turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id"), nullable=False, index=True)
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    call: Mapped[Call] = relationship(back_populates="turns")


class Callback(Base, TimestampMixin):
    __tablename__ = "callbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"), nullable=False, index=True)
    counsellor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class DNDEntry(Base, TimestampMixin):
    __tablename__ = "dnd_list"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
