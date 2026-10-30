from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class LeadIn(BaseModel):
    name: str | None = None
    phone: str = Field(..., min_length=10)
    email: EmailStr | None = None
    program_interest: str | None = None
    preferred_language: str | None = None
    source: str | None = None
    notes: str | None = None


class LeadOut(LeadIn):
    id: int
    status: str
    dnd: bool
    attempt_count: int
    last_attempted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Phase 1 additions (2026-06-05): kanban + timeline lifecycle.
    # All nullable so existing leads don't need a backfill.
    lead_temperature: str | None = None
    last_disposition: str | None = None
    last_disposition_at: datetime | None = None
    locked_by_user_id: int | None = None
    locked_until_at: datetime | None = None
    assigned_counsellor_id: int | None = None

    class Config:
        from_attributes = True


class LeadUpdate(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    program_interest: str | None = None
    preferred_language: str | None = None
    status: str | None = None
    dnd: bool | None = None
    notes: str | None = None


class LeadList(BaseModel):
    total: int
    items: list[LeadOut]


class CsvUploadResult(BaseModel):
    inserted: int
    updated: int
    skipped: int
    errors: list[str] = []
