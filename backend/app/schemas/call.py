from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TurnOut(BaseModel):
    id: int
    turn_index: int
    role: str
    text: str | None
    audio_path: str | None
    latency_ms: int | None
    created_at: datetime

    class Config:
        from_attributes = True


class CallOut(BaseModel):
    id: int
    lead_id: int
    campaign_id: int | None
    assigned_counsellor_id: int | None
    vobiz_call_id: str | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_s: float | None
    language: str | None
    outcome: str | None
    summary: str | None
    recording_path: str | None
    ttfa_p50_ms: int | None
    # Post-call lead-flow classifier output. NULL on no_speech calls or if
    # the classifier didn't run / failed.
    lead_temperature: str | None = None  # 'hot' | 'warm' | 'cold'
    lead_program: str | None = None
    lead_reasons: str | None = None
    caller_name: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class CallDetail(CallOut):
    turns: list[TurnOut]


class CallList(BaseModel):
    total: int
    items: list[CallOut]
