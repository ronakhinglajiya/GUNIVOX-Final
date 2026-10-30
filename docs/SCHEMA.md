# Gunivox — Data Schema

> Generated 2026-06-05. Source of truth is `backend/app/db/models.py`.
> Live database is SQLite at `backend/data/gunivox.db` (Alembic-migrated,
> head = `0003_user_last_seen`).

## At a glance

```
              ┌────────────┐
              │   users    │  3 rows · admins + counsellors
              └─────┬──────┘
                    │ created_by / assigned / counsellor
        ┌───────────┼────────────┐
        │           │            │
   ┌────▼─────┐   ┌─▼────────┐ ┌─▼───────────┐
   │campaigns │   │  calls   │ │  callbacks  │
   │  13 rows │   │2216 rows │ │   0 rows    │
   └────┬─────┘   └─────┬────┘ └─────┬───────┘
        │ campaign_id   │ lead_id    │ lead_id
        │               │            │
        │     ┌─────────▼────────────▼─────────┐
        │     │             leads               │  2102 rows
        │     │  (deduped by phone)             │
        │     └─────────────────────────────────┘
        │               │
        │               │ call_id
   ┌────▼──────────┐  ┌─▼──────────┐
   │ campaign_leads│  │   turns    │  1078 rows
   │   2099 rows   │  │ (utterances)│
   └───────────────┘  └────────────┘

         ┌───────────┐    ┌───────────┐
         │ dnd_list  │    │ audit_log │
         │  0 rows   │    │ 120 rows  │
         └───────────┘    └───────────┘ 
```

## Tables

### `users` — operators of the platform
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| email | VARCHAR(255) UNIQUE | login identity |
| password_hash | VARCHAR(255) | bcrypt |
| name | VARCHAR(255) | display name |
| phone | VARCHAR(20) NULL | |
| role | VARCHAR(20) | `admin` \| `counsellor` |
| is_available | BOOLEAN | warm-transfer eligibility |
| is_active | BOOLEAN | soft-delete flag |
| last_seen_at | DATETIME NULL | presence (admin Team page) |
| created_at / updated_at | DATETIME | inherited from `TimestampMixin` |

### `leads` — every prospect, deduped by phone
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| name | VARCHAR(255) NULL | extracted from CSV (col E + G) |
| phone | VARCHAR(20) UNIQUE | E.164-ish, dedupe key |
| email | VARCHAR(255) NULL | |
| program_interest | VARCHAR(255) NULL | what they expressed interest in |
| preferred_language | VARCHAR(10) NULL | `gu` / `en` / `hi` |
| source | VARCHAR(50) NULL | `csv` / `webhook` / `manual` |
| status | VARCHAR(30) | `new` → `queued` → `in_progress` → terminal |
| dnd | BOOLEAN | do-not-disturb flag |
| attempt_count | INTEGER | dial attempts so far |
| last_attempted_at | DATETIME NULL | for retry-policy gating |
| notes | TEXT NULL | free-text |

### `campaigns` — outbound dialing batches
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| name | VARCHAR(255) | |
| created_by_id | FK → users.id | who launched it |
| status | VARCHAR(20) | `draft` \| `running` \| `paused` \| `completed` |
| working_hours | JSON NULL | dial-window policy |
| retry_policy | JSON NULL | back-off rules |
| script_template | TEXT NULL | bespoke prompt overrides |

### `campaign_leads` — many-to-many between campaigns and leads
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| campaign_id | FK → campaigns.id | indexed |
| lead_id | FK → leads.id | indexed |
| status | VARCHAR(30) | per-campaign lifecycle |
| attempts | INTEGER | dial attempts in THIS campaign |
| **uniq** | `(campaign_id, lead_id)` | one row per lead per campaign |

### `calls` — every outbound conversation
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| lead_id | FK → leads.id | indexed |
| campaign_id | FK → campaigns.id NULL | indexed; NULL for ad-hoc calls |
| assigned_counsellor_id | FK → users.id NULL | who placed it |
| vobiz_call_id | VARCHAR(100) UNIQUE NULL | telephony provider id |
| started_at / ended_at | DATETIME NULL | UTC |
| duration_s | FLOAT NULL | wall-clock seconds |
| language | VARCHAR(10) NULL | detected at runtime |
| outcome | VARCHAR(30) NULL | `completed` / `no_answer` / `busy` / `failed` / `transferred` / `callback_scheduled` |
| summary | TEXT NULL | LLM-written 2-line recap |
| recording_path | VARCHAR(500) NULL | WAV file on disk |
| ttfa_p50_ms | INTEGER NULL | time-to-first-audio quality metric |
| lead_temperature | VARCHAR(10) NULL | `hot` \| `warm` \| `cold` (post-call classifier) |
| lead_program | VARCHAR(120) NULL | program of interest extracted |
| lead_reasons | TEXT NULL | classifier's reasoning |
| caller_name | VARCHAR(120) NULL | pre-filled from CSV, or transcribed |

### `turns` — single utterances inside a call
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| call_id | FK → calls.id | indexed; cascade-delete |
| turn_index | INTEGER | ordinal within the call |
| role | VARCHAR(20) | `user` \| `assistant` \| `system` |
| text | TEXT NULL | transcribed / synthesized text |
| audio_path | VARCHAR(500) NULL | per-turn audio chunk |
| latency_ms | INTEGER NULL | turn latency for diagnostics |

### `callbacks` — scheduled return-calls
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| lead_id | FK → leads.id | indexed |
| counsellor_id | FK → users.id NULL | assigned-to |
| scheduled_at | DATETIME | when to call back |
| status | VARCHAR(20) | `pending` / `done` / `missed` / `cancelled` |
| notes | TEXT NULL | |

### `dnd_list` — global do-not-disturb registry
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| phone | VARCHAR(20) UNIQUE | indexed |
| reason | VARCHAR(255) NULL | |

### `audit_log` — every privileged action
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| user_id | FK → users.id NULL | who did it |
| action | VARCHAR(100) | `campaign.start`, `lead.upload`, `call.originate`, ... |
| entity | VARCHAR(50) NULL | target type |
| entity_id | INTEGER NULL | target row id |
| details | JSON NULL | extra context |

## Why this shape

- **Calls live forever even if a campaign is deleted** — `calls.campaign_id` is nullable. Audit retention beats cascade convenience.
- **Leads dedupe by phone** — the same person isn't created twice across CSV uploads. The `campaign_leads` junction table is where per-campaign state lives (status, attempt count).
- **Turns cascade-delete with calls** — they're useless without the call context, so we let them go to keep the DB lean.
- **Counsellor / admin presence** is a single column (`users.last_seen_at`) updated by an auth-dependency middleware, throttled to 1 write / 60s per user. Powers the admin Team page without a separate sessions table.
- **`vobiz_call_id` is UNIQUE-but-nullable** — we can record a call before the telephony provider returns its id, then patch it in.
- **Hot/warm/cold classification is denormalised on `calls`** — keeps Reports queries to a single table.

## Live row counts (2026-06-05)
```
users               3
leads            2102
campaigns          13
campaign_leads   2099
calls            2216
turns            1078
callbacks           0
dnd_list            0
audit_log         120
```

## Migration history
| Revision | Date | What |
|---|---|---|
| `0001_initial` | 2026-05-17 | Bootstrap — every table above except the columns below |
| `0002_lead_flow` | 2026-05-18 | `calls.lead_temperature`, `lead_program`, `lead_reasons`, `caller_name` |
| `0003_user_last_seen` | 2026-06-05 | `users.last_seen_at` (admin Team page presence) |
