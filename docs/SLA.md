# Gunivox — SLA Logic

> Source of truth: [backend/app/api/analytics.py](../backend/app/api/analytics.py) `_build_sla()`.
> Surface: red banner at the top of the admin Analytics Hub (`/analytics`).

The SLA banner is the one widget on Analytics that **ignores the date
filter**. It's always asking: *what's broken at this exact moment that
needs human attention?* If you see anything red here, it's a *now*
problem, not a historical fact.

Three independent checks. Each one runs as a single SQL query against
the live DB.

---

## 1. Hot leads unclaimed > 30 min

### The check

A lead matches if **all** of these are true:

- It has a `temperature_set` event with `payload.to = "hot"` that was
  written **more than 30 minutes ago**
- Its **current** `Lead.lead_temperature` is still `"hot"` (counsellor
  hasn't downgraded it via override)
- `Lead.locked_by_user_id IS NULL` (nobody's currently holding it)
- `Lead.dnd = False` (it's not on the DNC list)

### Why these four conditions together

| Condition | Reason |
|---|---|
| Event >30 min ago | Proves the lead *became hot more than 30 min ago* |
| Current temp still hot | Prevents a stale `temperature_set` event from firing if the lead was downgraded since |
| Not currently locked | If someone's working it *right now*, that's not a breach — that's the system working |
| Not on DNC | We wouldn't dial them anyway |

### The 30-min threshold

Industry research says hot-lead response within 5 min is roughly
**10× more likely to convert than 30 min**. We use 30 min as the
*breach* threshold (not the *target*) because below that you're still
in the recoverable zone — only above 30 do you actively start losing
the lead.

### What surfaces

A red banner at the top of `/analytics`. The number shown is the count
of distinct `lead_id`s matching the four conditions.

---

## 2. Callbacks overdue

### The check

A row in the `callbacks` table matches if:

- `scheduled_at < now()` (the promised time has passed)
- `status = "pending"` (nobody marked it done, missed, or cancelled)

### Why this matters

When a counsellor saves disposition `callback_requested` in the Inbox
modal, the unlock endpoint auto-inserts a `callbacks` row with the
user-supplied `scheduled_at`. If that time arrives and no one acts on
it, the lead is being neglected **against an explicit promise** —
worse than not calling at all.

### No grace period

This check is binary — the moment `scheduled_at < now()`, it's
overdue. We don't fudge with "+5 minutes" because the operator needs
to *see* the slip immediately to choose whether to forgive it or chase.

---

## 3. Locks orphaned

### The check

A `Lead` row matches if:

- `Lead.locked_until_at < now()` (the 5-min lock window has expired)
- `Lead.locked_by_user_id IS NOT NULL` (the lock column still points at a user)

### What's actually happening

The background sweep loop runs **every 30 seconds** and clears expired
locks while emitting `LOCK_EXPIRED` events. This SLA check is
essentially a **sanity probe** — if any orphaned locks exist, it means
the sweep loop is either dead, slow, or wasn't started.

In a healthy system this number should be 0 or 1 at any given moment
(one row that just expired in the last 30s).

### Why it's worth surfacing

A jammed sweep loop would silently block leads from being re-claimed
by other counsellors — a card would look "Locked by Keval" forever
even after Keval walked away. Showing this as an SLA breach is the
**canary** for that failure mode.

---

## What does NOT trigger SLA (deliberately)

These would create noise without value:

| Excluded | Why |
|---|---|
| Unclassified leads in the queue | `lead_temperature IS NULL` means the AI hasn't called yet. That's a *dialer throughput* issue, not an SLA issue. Monitor it via Pipeline Cold lane underflow if needed. |
| Cold/warm leads waiting | Cold and warm don't have a response-time SLA — they're for nurture, not urgent action. |
| Locks within their 5-min window | A live lock is the *desired* state — someone is actively working the lead. |
| Callbacks scheduled in the future | Only past-due callbacks count. |

---

## Response playbook for the admin

| Breach | Suggested response |
|---|---|
| **Hot unclaimed >30 min** | Tell a counsellor to grab it from Inbox right now, or admin claims it themselves |
| **Callback overdue** | Reassign or call manually; if the lead has gone cold by now, mark cancelled with a reason |
| **Locks orphaned** | Restart the backend — the sweep loop has likely died |

---

## How each clears automatically

The banner is a single GET away from being green — no manual
acknowledgement step. Next poll (every 30s on the Analytics page), it
updates automatically.

| Breach | Clears when |
|---|---|
| **Hot unclaimed** | Someone POSTs `/api/leads/{id}/lock` successfully, OR the counsellor downgrades temperature via override |
| **Callback overdue** | Status moves from `pending` to `done` / `missed` / `cancelled` |
| **Lock orphaned** | The 30-second sweep loop runs and clears `locked_by_user_id` + `locked_until_at`, emitting `LOCK_EXPIRED` |

---

## Where the data comes from

| Check | Tables read | Cost |
|---|---|---|
| Hot unclaimed | `lead_events` ⋈ `leads` (with JSON path on payload) | Cheap — indexed on `event_type`, small filter set |
| Callbacks overdue | `callbacks` | Tiny — typically <100 rows total |
| Locks orphaned | `leads` (one indexed scan on `locked_until_at`) | Tiny — typically 0-3 matches |

Total SLA computation is ~3 SQL queries that run in <50ms even at 10k
leads. It's safe to compute on every Analytics page-load (every 30s
during admin use).

---

## Tuning hooks

| Lever | Where | Effect |
|---|---|---|
| Change 30-min threshold for hot SLA | `HOT_SLA = timedelta(minutes=30)` in `analytics.py` | Stricter SLA → more red banners |
| Add a 4th SLA check (e.g. "campaigns paused >24h") | Extend `_build_sla()` | New dimension visible to admin |
| Suppress lock-orphan check | Comment out the `orphans` block | Useful in dev where the sweep loop isn't running |
| Per-counsellor SLA breakdown | Group `_build_sla()` by `actor_user_id` | Would feed a "SLA per counsellor" leaderboard column |
