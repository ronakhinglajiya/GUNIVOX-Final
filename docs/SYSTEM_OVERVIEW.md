# Gunivox — System Overview (Features, Functionality & Flow)

**Gunivox** is an autonomous outbound **admissions voice agent** for **Ganpat University**. It calls prospective students in **Gujarati**, has a natural spoken conversation about programs/fees/eligibility, scores their interest, and either transfers them to a human counsellor or logs a follow-up. A web dashboard lets staff run calling campaigns, watch live calls, work an inbox of warm leads, and pull reports.

> Author's note: this document describes the system as deployed. Sections marked **[campaign]** describe the currently-active B.Sc IT campaign and are toggled by config, not hard-coded behavior.

---

## 1. Tech Stack

| Layer | Technology |
|---|---|
| Backend API | **FastAPI** (Python 3.13), Uvicorn |
| DB / ORM | **SQLite** + SQLAlchemy (async) + Alembic migrations |
      | LLM | **OpenAI gpt-4o-mini** (chat + embeddings) |
| Speech-to-Text | **Sarvam STT** (batch HTTP) + local VAD |
| Text-to-Speech | **Sarvam Bulbul v2** (streaming WebSocket) |
| Retrieval | **FAISS** vector index over the program catalog (RAG) |
| Telephony | **Vobiz** (Plivo-shape API) — outbound calls + media WebSocket |
| Frontend | **Next.js 15** (App Router) + Tailwind, TypeScript |
| Auth | JWT (localStorage) + RBAC (admin / counsellor) |
| Exposure | **Cloudflare Tunnel** → `api.gunivox.online`, `app.gunivox.online` |

**Ports:** backend runs on `:8000` (the Cloudflare tunnel points here). Frontend prod build served separately.

---

## 2. High-Level Architecture

```
                       ┌────────────────────────────────────────────┐
   Prospective         │              GUNIVOX BACKEND (:8000)         │
   student's phone     │                                              │
        ▲              │  ┌──────────────┐   ┌──────────────────────┐ │
        │  PSTN        │  │ Dialer worker│──▶│ Vobiz outbound call  │ │
        │              │  │ (background) │   └──────────┬───────────┘ │
        ▼              │  └──────────────┘              │             │
   ┌─────────┐  media  │  ┌─────────────────────────────▼───────────┐ │
   │  Vobiz  │◀───WS───┼─▶│  Telephony Bridge (per-call state machine)│ │
   └─────────┘         │  │   greet → listen(VAD) → STT → BRAIN → TTS │ │
                       │  └──────────────┬───────────────────────────┘ │
                       │                 │                              │
                       │   ┌─────────────▼──────────────┐               │
                       │   │  BRAIN (engine.py)          │               │
                       │   │  RAG + slots + fact-guard   │──▶ OpenAI     │
                       │   │  + campaign rules           │──▶ FAISS      │
                       │   └─────────────────────────────┘               │
                       │                 │                              │
                       │            ┌────▼────┐                         │
                       │            │ SQLite  │ leads, calls, turns,    │
                       │            │         │ campaigns, events…      │
                       │            └────┬────┘                         │
                       └─────────────────┼────────────────────────────┘
                                         │  REST + WebSocket
                              ┌──────────▼───────────┐
                              │  Next.js Dashboard   │  app.gunivox.online
                              │  (staff / counsellor)│
                              └──────────────────────┘
```

**Background workers (started at app boot, `main.py` lifespan):**
- **Dialer** — drives outbound campaigns (see §5).
- **Warm loop** — every 60s pings OpenAI/Sarvam so the first real call has no cold-start latency.
- **Lock sweep** — releases expired inbox locks.
- **Prewarm** — one-shot warm of embeddings + chat + TTS WS at startup.

---

## 3. The Voice Call Flow (core of the product)

This is what happens end-to-end for one outbound call. Implemented in `app/telephony/bridge.py` as a per-call async state machine over the Vobiz media WebSocket.

1. **Origination.** The dialer picks an eligible lead and asks Vobiz to place a call. Vobiz dials the number; when answered it opens a **media WebSocket** back to the bridge and streams the caller's audio.

2. **Greeting.** The bridge immediately plays the pre-rendered **greet** audio (`data/fillers/gu/greet.wav`). The caller can **barge in** (talk over it) — local VAD detects speech and cancels the greet. **[campaign]** The greeting currently opens with the commerce-career / *Department of Computer Science* framing and asks the caller's name.

3. **Listen (VAD).** Incoming PCM audio is buffered; a **local Voice Activity Detector** (`app/stt/vad.py`) segments it into complete utterances (detects speech start/end). Sarvam's *streaming* STT proved unreliable, so we use **batch STT**: once an utterance completes, the whole clip is sent to Sarvam over HTTP.

4. **Transcribe.** Sarvam returns Gujarati text + detected language. Echo/announcement filtering drops audio that is actually our own TTS bleeding back through the phone speaker.

5. **Intent check.** Fast keyword pass (`app/conversation/intent.py`): if the caller clearly wants a human → **handoff**; if they say goodbye → wrap up. Otherwise → normal brain turn.

6. **Brain turn** (`app/brain/engine.py`) — see §4. Produces a streamed Gujarati reply grounded in the program catalog and campaign rules.

7. **Speak (TTS).** The reply text is cleaned by the **normalizer** (`text_normalize.py`) then streamed to **Sarvam Bulbul v2** over a persistent WebSocket; PCM audio streams back to Vobiz → the caller hears it with ~2.8s time-to-first-audio.

8. **Barge-in / turn-taking.** If the caller speaks while the AI is talking, the current TTS is cancelled and we listen again — natural interruption.

9. **Loop** steps 3–8 until the call ends, a transfer happens, or a dead-silence watchdog hangs up.

10. **Handoff / callback.** On a hot lead or explicit request, the bridge tries a **warm transfer** to a counsellor (Vobiz conference); if none available it offers a callback.

11. **Finalize** (`_finalize_call`). On hangup: persist all **turns**, the call record (duration, outcome, temperature), settle the `campaign_lead` status so the dialer can advance, and flush the call recording WAV.

**Latency techniques:** pre-recorded filler audio ("thinking" clips) mask LLM/TTS warmup; persistent TTS WebSocket per call; embed cache; periodic warm loop; first-word prompt tuning.

---

## 4. The AI Brain

The brain turns one caller utterance into one grounded spoken reply. Key pieces:

### 4.1 RAG (Retrieval-Augmented Generation)
- The program catalog (`data/admission_brain.json`, 126 program records) is embedded into a **FAISS index** (`gunivox_brain.index`).
- Each turn, the caller's utterance (or a short context window for follow-ups) is embedded and the top-k matching program records are retrieved (`app/brain/rag.py`).
- Retrieved records become the LLM's **reference context** — the agent answers *only* from this, so fees/eligibility/counsellor info are grounded, not hallucinated.

### 4.2 Session slots (steering the LLM)
`engine.py` assembles a `SESSION SLOTS` system message every turn — high-priority directives layered above the retrieved context:
- **Caller name** — remembered, never re-asked.
- **CAMPAIGN_RULE** **[campaign]** — the top-priority lock (see §6).
- **PRIORITY_OFFER** — the opening pitch on turn 1.
- **USER_BACKGROUND** — reacts to the caller's stated 12th-grade stream (arts/commerce/PCM/PCB).
- **ACADEMIC_LEVEL** — 10th → diploma only, 12th → bachelor only, graduate → master only.
- **ADMISSION_TIMING** — always says "admissions are open now", never a month.
- Focused-program pin, degree-ambiguity disambiguation, etc.

### 4.3 Fact-guard (`fact_guard.py`)
A whitelist of every fee/phone/email/counsellor is parsed from the pinned context. Every TTS-bound chunk is validated — any fee or phone number the LLM emits that isn't in the whitelist is **auto-corrected or stripped** before it's spoken. This is what prevents wrong fees or wrong counsellor numbers from reaching the caller.

### 4.4 TTS text normalization (`text_normalize.py`)
Cleans text so Sarvam Gujarati TTS reads it naturally:
- Program codes → Gujarati script (`B.Tech → બીટેક`, `B.Sc IT → બી એસસી, આઈટી`).
- Loan words, subjects, titles (`Dr. → ડૉક્ટર`), counsellor names (`Amit Suthar → અમિત સુથાર`).
- **Phone numbers** → digit-by-digit in **Gujarati** words with sentence-break pauses so callers can write them down.
- Strips punctuation Sarvam would otherwise vocalize ("dot", "bracket").

### 4.5 Conversation intelligence (`app/conversation/`)
- **lead_scoring** — assigns a temperature (hot / warm / cold) from the transcript.
- **signals / dialog / proactive** — per-turn hints (e.g. detected objections, next-best action).
- **state / memory** — conversation state brief carried across history truncation.
- **language** — Gujarati/Hindi/English handling.

---

## 5. Campaigns & the Dialer

**Campaigns** are batches of leads dialed automatically.

- **Create a campaign:** upload a CSV via *Campaigns ▸ Upload CSV & start dialing* (`/api/campaigns/upload-and-start`). The parser detects a 10-digit Indian phone in **column L** (with a fallback that scans any cell), plus first/last name from columns E/G when present. Each row is upserted as a **Lead** and attached to a new campaign.
- **The dialer worker** (`app/campaigns/dialer.py`) runs in the background:
  - Every tick, for each **active** campaign inside its working-hours window, it picks the **next eligible lead** (not DND, attempts < max, last-attempt older than the gap) and originates a call.
  - **Concurrency is DB-driven, two-tier:** a **per-campaign** cap (default 1 → strictly one call at a time per campaign) and a **global** cap (default 5) across all campaigns. Counts live `in_progress` `campaign_lead` rows, so a call is "in flight" until the bridge actually settles it — no call-bombing.
  - A **stale-recovery** safety net resurrects any `campaign_lead` stuck `in_progress` (e.g. a crashed bridge) so a campaign never freezes.
  - **Retry:** failed/no-answer leads can be re-queued in bulk ("Retry failed") without re-uploading.
- **Campaign controls:** start / pause / delete; per-campaign concurrency for multi-PC dialing.

---

## 6. Active Campaign Configuration **[campaign]**

Currently the agent runs a **B.Sc IT-only** campaign targeting **12th-Commerce** students. Toggled by config flags in `app/config.py` (`bsc_it_only_campaign=True`; the older `bca_only_campaign` is off).

- **Sells only B.Sc IT** at the **DCS Main Campus** (the DCS *Ahmedabad* campus is hard-filtered out of RAG, family, and stream results).
- Behaves like a **persuasive human counsellor**, not a menu — a 3-step pitch (value → specialisation interest → name the variant + offer counsellor). Never recites all 4 variants at once.
- **Pivot behavior:** if a caller names any other course (BCA/BBA/B.Tech/…), it briefly acknowledges it's offered, then redirects to B.Sc IT's value — never quotes fees/duration for the other course. A guard drops any non-target `focused_program` so RAG can't leak another program's fee.
- **Counsellor lock:** every counsellor reference is forced to **Dr. Amit Suthar, 9998583001** (one counsellor, never two). Enforced both in the reference context and the fact-guard whitelist.
- **No bridge course** is ever mentioned; B.Sc IT is stated as open to all streams.
- B.Sc IT Hons is a **4-year** degree.

All of the above reverts cleanly by flipping the campaign flag off.

---

## 7. Inbox, Pipeline & Lead Lifecycle

- **Inbox** (`/api/inbox`) — warm/hot/cold buckets of leads for **counsellors** to work. A counsellor **locks** a lead (atomic SQL lock with a countdown), reviews the AI transcript + history, calls the student, then sets a **disposition** (Interested / Not interested) + notes. Locks auto-expire (lock-sweep worker).
- **Disposition mapping** drives funnel stage, workflow status, and DND — recorded on an append-only **`lead_events`** timeline.
- **Pipeline** (`/api/pipeline`) — a funnel/date-filterable view of every lead's stage.
- **Callbacks** and **Closures** — scheduled follow-ups and won/lost outcomes.
- **DND list** — numbers marked do-not-call are never dialed again.

---

## 8. Data Model (SQLite tables)

| Table | Purpose |
|---|---|
| `users` | Staff accounts (admin / counsellor), auth, RBAC |
| `leads` | The prospective students — phone, name, temperature, disposition, funnel stage, DND, attempt count |
| `lead_events` | Append-only timeline (calls, notes, disposition, temperature changes) |
| `campaigns` | A batch of leads to dial, with status + working-hours + retry policy |
| `campaign_leads` | Junction: which leads are in a campaign + per-campaign status/attempts |
| `calls` | One row per call attempt — duration, outcome, temperature, recording |
| `turns` | Every utterance (caller + AI) in a call, ordered |
| `callbacks` | Scheduled follow-up requests |
| `dnd_list` | Do-not-call numbers |
| `audit_log` | Who did what (campaign start/pause/delete, uploads, etc.) |

---

## 9. Backend API Surface

Routers are mounted in `app/main.py`. Major areas:

| Area | Router | What it does |
|---|---|---|
| Health | `health` | `/api/health/full` — DB/OpenAI/Sarvam/Vobiz readiness |
| Auth | `auth` | login, signup, forgot/reset password, `/me` |
| Leads | `leads` | CRUD, CSV upload/upsert, per-lead notes |
| Calls | `calls`, `calls_browse` | originate, list/browse, transcripts |
| Campaigns | `campaigns` | create, upload-and-start, attach, start/pause/delete, retry, progress |
| Inbox | `inbox` | warm-lead buckets, lock/unlock, disposition |
| Pipeline | `pipeline` | funnel view + filters |
| Callbacks / Closures | `callbacks`, `closures` | follow-ups, won/lost |
| Dashboard / Analytics | `dashboard`, `analytics` | KPIs, team visibility, charts |
| Reports | `reports` | CSV + PDF exports (date + temperature filters) |
| Billing / Usage | `billing` | usage metering |
| Team | `team` | staff management (admin) |
| Live | `live` | WebSocket for live-call monitoring in the UI |
| Telephony | `vobiz_webhooks`, `vobiz_bridge`, `mock_vobiz` | call status webhooks + media bridge + local mock for testing |
| Admin | `admin_cleanup` | maintenance endpoints |

---

## 10. Frontend (Next.js dashboard)

Route group `app/(app)/` (auth-gated) + public auth pages. See `docs/FRONTEND.md` for the full component/UX reference.

| Page | Purpose |
|---|---|
| Dashboard | KPIs, system health, team overview |
| Campaigns | Upload CSV, start/pause, progress cards, retry failed |
| Dialer | Dedicated live dialing view + hangup |
| Inbox | Warm-lead worklist for counsellors (lock → transcript → disposition) |
| Leads / Leads[id] | Lead list + CRM-style lead detail (collapsible date-grouped timeline, last AI conversation, inline notes) |
| Pipeline | Funnel with date filter |
| Calls / Calls[id] | Call log + transcript viewer |
| Callbacks / Closures | Follow-ups + outcomes |
| Analytics | Charts (Recharts) |
| Reports | CSV/PDF export |
| Usage | Metering (no currency shown) |
| Team | Staff management (admin) |
| Settings | Config |
| login / signup / forgot / reset | Auth |

**Auth gating:** inline `<head>` script + `(app)/layout.tsx` effect + `<RequireAdmin>` wrapper + sidebar role filter. Phone masking for counsellors.

---

## 11. Telephony (Vobiz) Integration

- Vobiz uses a **Plivo-shape** API. Outbound calls are originated from `app/telephony/vobiz.py`.
- When answered, Vobiz connects a **media WebSocket** to the bridge (`audioTrack="inbound"`, no-cache headers).
- **Warm transfer** uses a **conference** — the caller and counsellor are bridged.
- A **status webhook** (`vobiz_webhooks.py`) settles call outcomes even if the media WS never cleanly closed.
- `mock_vobiz.py` lets you exercise the whole brain locally without placing real calls.

---

## 12. Auth & RBAC

- **JWT** stored in localStorage; `lib/api.ts` attaches it to every request.
- Roles: **admin** (full access, team management) and **counsellor** (campaigns parity, inbox, phone-masked views).
- Signup / forgot-password / reset flows included.

---

## 13. Deployment & Operations

- **Backend:** `venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000` (via `gunivox.bat`, which also supervises/auto-restarts). LAN-ready, scalable across PCs via per-campaign concurrency.
- **Exposure:** Cloudflare Tunnel maps `api.gunivox.online` → `localhost:8000` and `app.gunivox.online` → the frontend.
- **Frontend:** Next.js production build (`npm run build`) with `NEXT_PUBLIC_API_URL` baked in.
- **Fillers:** pre-rendered once via `scripts/generate_fillers.py`; regenerate a WAV by editing `DEFAULT_FILLERS` and deleting the old `.wav` (the generator skips existing files).
- **Port hygiene:** the backend must own port 8000 (the tunnel target). If another app (e.g. a second project) grabs 8000, every Gunivox API call 404s → "network error" in the UI. Fix by freeing 8000 and restarting the Gunivox backend.
- **After any backend code change** (engine/config/normalize/fillers): **restart the backend** — modules and filler audio are loaded once at boot.

---

## 14. Change Checklist (how to safely update behavior)

| To change… | Edit | Then |
|---|---|---|
| What the agent pitches / campaign rules | `app/brain/engine.py` (slots) + `app/config.py` (flags) | restart backend |
| Pronunciation / TTS text | `app/brain/text_normalize.py` | restart backend |
| The opening greeting audio | `app/brain/fillers.py` → delete `data/fillers/gu/greet.wav` → run `scripts/generate_fillers.py` | restart backend |
| Program data / fees / counsellors | `data/admission_brain.json` | rebuild FAISS index if embeddings text changed |
| CSV upload rules | `app/api/leads.py` | restart backend |
| Dashboard UI | `frontend/app/(app)/…` | `npm run build` + restart frontend |

---

*Generated as a living reference for the Gunivox admissions voice agent. Sections tagged **[campaign]** reflect the currently-active B.Sc IT / DCS Main Campus configuration and are controlled by config flags.*
