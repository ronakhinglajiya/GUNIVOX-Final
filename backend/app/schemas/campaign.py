from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WorkingHours(BaseModel):
    """When the dialer is allowed to place calls.

    `days` is a list of weekday integers (Monday=0..Sunday=6).
    `start` / `end` are 24h "HH:MM" strings in `timezone` (default Asia/Kolkata).
    """

    timezone: str = "Asia/Kolkata"
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5])
    start: str = "10:00"
    end: str = "19:00"


class RetryPolicy(BaseModel):
    max_attempts: int = 3
    gap_hours: float = 4.0


class CampaignIn(BaseModel):
    name: str
    working_hours: WorkingHours | None = None
    retry_policy: RetryPolicy | None = None
    script_template: str | None = None


class CampaignOut(BaseModel):
    id: int
    name: str
    status: str
    created_by_id: int
    working_hours: dict[str, Any] | None
    retry_policy: dict[str, Any] | None
    script_template: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CampaignList(BaseModel):
    total: int
    items: list[CampaignOut]


class CampaignUpdate(BaseModel):
    name: str | None = None
    status: str | None = None  # draft, active, paused, done
    working_hours: WorkingHours | None = None
    retry_policy: RetryPolicy | None = None
    script_template: str | None = None


class AttachLeadsIn(BaseModel):
    lead_ids: list[int]


class CampaignLeadOut(BaseModel):
    id: int
    campaign_id: int
    lead_id: int
    status: str
    attempts: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CampaignLeadList(BaseModel):
    total: int
    items: list[CampaignLeadOut]
