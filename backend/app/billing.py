"""Per-call billing — pure functions, no DB.

Tariff (set by user 2026-06-02):
  Rate:           ₹2.69 per minute (= ₹0.044833… per second)
  Slab 1: 1-30s   → bill 30s flat   (= ₹1.345)
  Slab 2: >30s    → round UP to the next full minute, bill at ₹2.69/min
  Slab 0: 0s (no audio frame ever arrived) → ₹0 (call never connected)

`Call.duration_s` is the TALK-TIME (from first media frame → call end),
NOT the WebSocket lifetime. This was a deliberate fix on 2026-06-02 because
the WS-lifetime version was overcharging by ~10s of ring time per call.

Billing epoch (`BILLING_EPOCH`): only calls created on or after this UTC
timestamp count. Existing test/dev calls (the 269 made before deployment)
are excluded — the user asked for a clean zero start at deploy time. Override
via the BILLING_EPOCH env var (ISO-8601), otherwise the default below holds.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timezone

# Per-minute rate in INR. Single source of truth — any UI must read this.
RATE_INR_PER_MINUTE: float = 2.69

# Per-second rate derived from the per-minute rate (for slab-1 calculation).
RATE_INR_PER_SECOND: float = RATE_INR_PER_MINUTE / 60.0

# Slab boundaries in seconds.
MIN_BILLABLE_SECONDS: int = 30  # any call 1-30s rounds UP to 30s
SECONDS_PER_MINUTE: int = 60

# Billing epoch. Anything created strictly before this is excluded from
# every billing total / list — they're treated as pre-deployment test
# traffic. Override at deploy time with:  BILLING_EPOCH=2026-06-02T18:30:00+05:30
#
# 2026-06-02 (bump 4 — clean-slate re-deploy with correct code): another
# full DB + recordings wipe after wiring strict 1-by-1 dialing and the
# DB-driven concurrency gate. Epoch reset to the exact UTC moment of the
# wipe — every call placed AFTER the next backend restart starts billing
# from zero.
_DEFAULT_EPOCH_ISO = "2026-06-02T11:28:19+00:00"


def _parse_epoch(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


BILLING_EPOCH: datetime = _parse_epoch(os.getenv("BILLING_EPOCH", _DEFAULT_EPOCH_ISO))


def billable_seconds(duration_s: float | None) -> int:
    """Return the chargeable seconds for a call of `duration_s` talk time.

    Rules:
      duration < 1        → 0  (never connected — sub-second, not a real call)
      1 ≤ duration ≤ 30   → 30 (min charge slab)
      duration > 30       → ceil(duration / 60) * 60 (round up to next minute)
    """
    if duration_s is None or duration_s < 1.0:
        return 0
    if duration_s <= MIN_BILLABLE_SECONDS:
        return MIN_BILLABLE_SECONDS
    return int(math.ceil(duration_s / SECONDS_PER_MINUTE)) * SECONDS_PER_MINUTE


def amount_inr(duration_s: float | None) -> float:
    """₹ amount for a call of `duration_s` talk time. Returns 2-decimal float."""
    secs = billable_seconds(duration_s)
    if secs == 0:
        return 0.0
    # Slab 1 uses the per-second rate exactly; slab 2 also reduces to the
    # same arithmetic (60s × per-second = ₹2.69 per minute). One formula
    # covers both, no special-case needed.
    return round(secs * RATE_INR_PER_SECOND, 2)


def is_billable_period(created_at: datetime | None) -> bool:
    """True if a call's created_at is at/after the billing epoch."""
    if created_at is None:
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at >= BILLING_EPOCH
