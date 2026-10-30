from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CallbackIn(BaseModel):
    lead_id: int
    scheduled_at: datetime
    counsellor_id: int | None = None
    notes: str | None = None


class CallbackOut(CallbackIn):
    id: int
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CallbackUpdate(BaseModel):
    scheduled_at: datetime | None = None
    counsellor_id: int | None = None
    status: str | None = None
    notes: str | None = None


class CallbackList(BaseModel):
    total: int
    items: list[CallbackOut]
