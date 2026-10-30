# Gunivox — Frontend Architecture

> Reference doc for re-building the frontend in another project. Covers
> the stack, every page, every shared context, every backend contract,
> and the patterns that hold it together.

---

## Stack

| Layer | Choice | Why |
|---|---|---|
| Framework | **Next.js 15.0.3 (App Router)** | File-based routing, route groups, layouts, server-rendered shell, client islands |
| Language | **TypeScript 5.6** | Strict types across pages + API helpers |
| Styling | **Tailwind CSS 3.4** | Utility-first, custom theme tokens (`bg-bg`, `text-ink`, `border-border`, `text-brand`, etc.) |
| Charts | **Recharts 3.8** | Used only on `/analytics` — kept off the hot path |
| Icons | **lucide-react 0.460** | One consistent icon set everywhere |
| Class compose | **clsx 2.1** | `clsx("base", active && "...")` everywhere instead of template strings |
| State | **React 18 + Context** | No Redux/Zustand. AuthContext, ToastContext, ThemeContext are the only globals. |
| Fetch | **Hand-rolled fetch wrapper** in `lib/api.ts` | Auto-attaches JWT, throws `ApiError`, handles 401 → bounce to login |

No service workers, no SSR data fetching, no React Query. Every page is `"use client"` and does its own `useEffect` + fetch.

---

## Routing structure

```
app/
├─ layout.tsx                 # root layout — providers + auth-guard <script>
├─ page.tsx                   # / → redirects to /dashboard or /login
├─ globals.css                # tailwind base + custom tokens
│
├─ login/                     ┐
├─ signup/                    │ PUBLIC routes (no auth required)
├─ forgot-password/           │
├─ reset-password/            ┘
│
└─ (app)/                     # Route group — every route here is auth-gated
   ├─ layout.tsx              # (app) layout — sidebar + topbar + auth redirect
   ├─ dashboard/              # KPI overview (counsellor + admin)
   ├─ inbox/                  # Counsellor kanban — lock + disposition workflow
   ├─ dialer/                 # Manual dial page (originate /api/calls/originate)
   ├─ pipeline/               # Strategic kanban: cold/warm/hot/closing
   ├─ leads/                  # List of every lead
   │  └─ [id]/                # Lead detail — sticky header + CRM timeline + add-note
   ├─ calls/                  # Call history list
   │  └─ [id]/                # Single call with full transcript + recording
   ├─ campaigns/              # CSV upload, start/pause, retry-failed
   ├─ callbacks/              # Scheduled callbacks list
   ├─ closures/               # Closing pipeline + DNC tab (admin)
   ├─ team/                   # Admin-only: presence + per-counsellor stats
   ├─ analytics/              # Admin-only: 7 widgets, 5 tabs, custom date ranges
   ├─ billing/                # Usage/billing (admin-only)
   ├─ reports/                # Admin-only: CSV/PDF downloads with filters
   └─ settings/               # Admin-only
```

**Convention**: any route inside `(app)/` is mounted under the auth-gated
layout. Adding a new authed page = create a folder under `(app)/`.

---

## Auth + RBAC

Two roles: `admin` and `counsellor`.

### Auth flow

1. `POST /api/auth/login` returns `{access_token, user}`
2. Token persisted to `localStorage` under key `gunivox.jwt`
3. `lib/api.ts` auto-attaches `Authorization: Bearer <token>` on every request
4. `lib/auth-context.tsx` exposes `user`, `isAdmin`, `loading`, `login()`, `logout()`, `refresh()`
5. `app/layout.tsx` inline `<script>` checks the token in localStorage **before React boots** — if missing on a protected path, redirects to `/login?next=...` before any pixel paints (no flash)

### Two layers of gating

| Layer | Where | What it does |
|---|---|---|
| Inline `<script>` in `<head>` | `app/layout.tsx` | Pre-React redirect. Fastest possible: no UI flash. |
| `(app)/layout.tsx` `useEffect` | `app/(app)/layout.tsx` | If `useAuth().user === null`, `router.replace("/login")` |
| `<RequireAdmin>` wrapper | `components/require-admin.tsx` | Wraps admin-only pages. Renders children only if `useAuth().isAdmin`. |
| Sidebar filter | `components/sidebar.tsx` | NAV items with `role: "admin"` are hidden from counsellor view |

Admin-only pages (each wrapped in `<RequireAdmin>`):
`/analytics`, `/team`, `/billing`, `/reports`, `/settings`, `/closures` (DNC tab).

---

## Per-page feature reference

### `/login`, `/signup`, `/forgot-password`, `/reset-password`
Public auth pages. Use a **mounted-gate pattern** (render a spinner placeholder
during SSR, mount the real form on first effect) to dodge LastPass/1Password
icon injection causing hydration mismatches. `useSearchParams()` reads
`?next=...` so after login the user lands back on the URL they originally
requested. Wrapped in `<Suspense>` because Next.js 15 requires it for
`useSearchParams` to avoid bailing out of static prerender.

### `/dashboard`
Hero stats + temperature tiles. Polls a backend KPI endpoint.

### `/inbox` (counsellor + admin)
- Kanban: **Locked by me · Hot · Warm · Cold**
- Date-range filter: switches to "history mode" showing only leads I
  disposition-saved between two IST dates (default empty = live mode)
- Click card → `POST /api/leads/{id}/lock` → modal opens with:
  - Countdown timer + `+5 min` button (capped 2 extensions)
  - **Rich Timeline panel** — previous notes (parsed by timestamp markers)
    + AI events; expand AI-call rows to lazy-fetch transcript
  - Disposition dropdown (**2 options**: Interested / Not interested)
  - Optional callback datetime when Interested
  - Notes textarea (≥10 chars required unless no_answer/busy/wrong_number)
- Admin-only "Delete unnamed" button (cascade-removes NULL-name leads)
- Polls `/api/inbox` every 5s in live mode (disabled in history mode)
- Toast notifications for new hot/warm leads via `lib/inbox-notifications.tsx`

### `/dialer`
Manual outbound dial. POST `/api/calls/originate` with `{to: phone}`.
Shows live call card with hangup button.

### `/pipeline`
Strategic kanban — 4 lanes (Cold/Warm/Hot/Closing), server-side bucketed
via `GET /api/pipeline`. Date filter: `?date=YYYY-MM-DD` switches from
current-state to "classifications recorded on this day" mode via
`lead_events`. No drag-and-drop — leads move through funnel only via
the disposition workflow.

### `/leads`
Table of every lead. Click a row → `/leads/[id]`. Search box.

### `/leads/[id]` (the **CRM timeline**)
- Top toolbar: Back + Refresh
- Lead header card (status, attempts, last call, disposition, temperature pill)
- **Last AI conversation panel** — auto-fetches the most recent call's
  transcript (any event with `payload.call_id`, not just `ai_call_ended`)
- **Add a note** textarea + Save button → `POST /api/leads/{id}/notes`
- **CRM-style collapsible timeline** — compact 1-line rows grouped by IST
  date, click to expand inline:
  - Note rows expand to show full text + author + timestamp
  - Any event-row with `payload.call_id` expands to show transcript inline
  - Other event rows expand to show raw JSON payload
  - Excel-style "Collapse all" / "Expand all" toggle

### `/calls`, `/calls/[id]`
Call list + single-call detail with full turn-by-turn transcript and
recording audio player.

### `/campaigns`
- Cards per campaign showing queued/in-progress/done/failed counts
- Start/Pause toggle (POST `/api/campaigns/{id}/start` or `/pause`)
- Attach existing leads modal
- **Retry failed** button per campaign + global "Retry failed (all)"
  → POST `/api/campaigns/{id}/retry_failed` (or `/retry_failed_all`)
- CSV upload + start-dialing flow

### `/callbacks`
Scheduled callbacks list with status (pending/done/missed/cancelled).

### `/closures`
Two tabs: **Closing pipeline** (applied/docs_received/enrolled) + **DNC list**.

### `/team` (admin)
Per-user presence (online/away/offline based on `last_seen_at`) + today's
calls + talk time + on-call indicator. Polls every 10s.

### `/analytics` (admin)
Single-page hub with 5 tabs: **Overview · Funnel · Counsellors · AI · Programs · Heatmap**.

Widgets:
1. Conversion funnel (6 stages with drop-off %)
2. AI performance tile (pickup, avg duration, median TTFA, sparkline)
3. Counsellor leaderboard (sortable table)
4. Live SLA banner (hot unclaimed >30 min, callbacks overdue, locks orphaned)
5. Outcome donut (Recharts PieChart)
6. 7×24 IST heatmap (pickup rate / hot rate toggle)
7. Program demand (stacked horizontal bar)

Window filter: Today · 7d · 30d · All. Plus custom date range with
From/To inputs (validates `to ≥ from`, disables auto-refresh when set).

### `/reports` (admin)
CSV + PDF downloads with date + temperature + outcome filters.
- Calls CSV (with flattened transcripts)
- Leads CSV (with activity stats)
- Summary PDF (visual report with branding header/footer)
- Leads & Transcripts PDF (per-lead with full conversation)
- Backfill names from CSV (retro-fix names without re-dialing)

### `/billing` (admin)
Per-call billable minutes + epoch-aware totals.

### `/settings`, admin Team / Reports / Usage / Settings entries — admin role only.

---

## Shared `lib/` modules

### `lib/api.ts` — fetch wrapper

```ts
const TOKEN_KEY = "gunivox.jwt";

export function getToken(): string | null
export function setToken(token: string | null): void

export const api = {
  get: <T>(path) => request<T>(path),
  post: <T>(path, body?) => request<T>(path, { method: "POST", body }),
  put: <T>(path, body?) => request<T>(path, { method: "PUT", body }),
  delete: <T>(path) => request<T>(path, { method: "DELETE" }),
  upload: <T>(path, file, extra?) => request<T>(path, { method: "POST", body: fd }),
};

export class ApiError extends Error {
  status: number;
  body: unknown;
}
```

Behaviour:
- Reads `NEXT_PUBLIC_API_URL` (`.env.local` / `.env.production`) for the base
- Adds JSON `Content-Type` header for object bodies; passes `FormData` through
- On `401`: `setToken(null)` + `window.location.href = "/login"`
- On any non-2xx: throws `ApiError(status, body)` so caller can match

### `lib/auth-context.tsx`

```ts
interface AuthContextValue {
  user: User | null;
  isAdmin: boolean;
  loading: boolean;
  login: (email, password, next?) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
}
```

`AuthProvider` initially `loading=true`, fetches `/api/me`, sets `user`,
sets `loading=false`. `login()` posts to `/api/auth/login`, stores token,
sets user, navigates to `next` URL (or `/dashboard`).

### `lib/toast-context.tsx`

```ts
interface ToastCtx {
  toast: (t: { kind, title, description? }) => void;
  success: (title, description?) => void;
  error: (title, description?) => void;
  info: (title, description?) => void;
}
```

IDs are a **monotonic counter** (not `Date.now()+Math.random()` — that
collides under float64 precision when ≥2 toasts fire in the same ms).
Auto-dismiss after 5s.

### `lib/theme-context.tsx`
Dark/light toggle. Persists to `localStorage["gunivox.theme"]`.

### `lib/inbox-notifications.tsx`
Global notifier that polls `/api/inbox` in the background, fires toasts
when new hot/warm leads appear. Provides `useInboxNotifier()` hook with
`markAllSeen()` so the badge can be cleared when the Inbox page is opened.

### `lib/live.ts`
SSE/event hook: `useLiveEvents((e) => ...)`. Subscribes to backend's
event stream for `call.ended`, `lead.created`, etc. Used by Pipeline +
Dashboard to refresh on real-time changes.

### `lib/phone.ts`
```ts
export function maskPhone(phone, isAdmin): string
```
No-op as of 2026-06-06 (counsellors see full numbers). Signature kept so
every call site keeps compiling.

### `lib/datetime.ts`
IST formatters used across pages.

### `lib/types.ts`
Shared TS interfaces: `User`, `Lead`, `LeadsList`, `Call`, `CallsList`,
`Campaign`, `CampaignsList`, etc.

---

## Backend API contracts (what the frontend expects)

### Auth
| Method · Path | Body | Returns |
|---|---|---|
| `POST /api/auth/login` | `{email, password}` | `{access_token, user}` |
| `POST /api/auth/signup` | `{email, password, name}` | `{access_token, user}` (counsellor only) |
| `POST /api/auth/forgot-password` | `{email}` | `{debug_token}` |
| `POST /api/auth/reset-password` | `{token, password}` | `{user}` |
| `GET /api/me` | — | `User` |

### Leads
| Method · Path | Notes |
|---|---|
| `GET /api/leads?limit=&offset=&q=` | List + search |
| `GET /api/leads/{id}` | Single lead detail |
| `POST /api/leads` | Create |
| `PUT /api/leads/{id}` | Update fields (incl. `dnd`) |
| `DELETE /api/leads/{id}` | Delete |
| `POST /api/leads/{id}/notes` | **Append timestamped note** |
| `GET /api/leads/{id}/timeline?limit=200` | Append-only event list |
| `POST /api/leads/{id}/lock` | Atomic acquire, returns 409 if held |
| `POST /api/leads/{id}/extend` | +5 min, capped at 2 extensions |
| `POST /api/leads/{id}/unlock` | `{disposition, notes, callback_at?, place_manual_call?}` |
| `POST /api/leads/upload` | CSV upload |
| `POST /api/leads/backfill_csv_names` | Retro-fix names |

### Inbox / Pipeline / Closures
| Method · Path | Notes |
|---|---|
| `GET /api/inbox?from=&to=` | Hot/warm/cold/mine buckets; optional history-mode date range |
| `GET /api/pipeline?date=YYYY-MM-DD` | Strategic kanban; optional historical mode |
| `GET /api/closures` | Closing pipeline + DNC list + totals |

### Calls
| Method · Path | Notes |
|---|---|
| `GET /api/calls?limit=&offset=&lead_id=` | Call list |
| `GET /api/calls/{id}` | Call detail + full turn array |
| `POST /api/calls/originate` | `{to, from?}` |
| `POST /api/calls/{id}/hangup` | End an active call |

### Campaigns
| Method · Path | Notes |
|---|---|
| `GET /api/campaigns?limit=&offset=` | Campaign list |
| `GET /api/campaigns/{id}/progress` | `{queued, in_progress, done, failed, failed_retryable, total}` |
| `POST /api/campaigns/{id}/start` | Set to active |
| `POST /api/campaigns/{id}/pause` | Set to paused |
| `POST /api/campaigns/{id}/retry_failed` | Reset failed → queued, auto-start |
| `POST /api/campaigns/retry_failed_all` | Same across all campaigns |
| `POST /api/campaigns/upload-and-start` | CSV upload + auto-start |

### Reports
| Method · Path | Filters |
|---|---|
| `GET /api/reports/calls.csv` | `?date=&temperature=&outcome=` |
| `GET /api/reports/leads.csv` | same |
| `GET /api/reports/summary.pdf` | same |
| `GET /api/reports/leads_transcripts.pdf` | same |

### Admin
| Method · Path | Notes |
|---|---|
| `GET /api/admin/team` | Presence + today's stats per user |
| `GET /api/admin/analytics?window=&from=&to=` | One-shot analytics payload |
| `GET /api/admin/unnamed_leads/preview` | Pre-flight count |
| `DELETE /api/admin/unnamed_leads` | Cascade-delete NULL-name leads |

---

## UI patterns worth copying

### 1. Auth-guard inline script
Inline `<script>` in `<head>` of root layout checks `localStorage` for the
JWT **before React even loads**. If missing on a protected path, redirects
to `/login?next=...` synchronously. No spinner flash.

```tsx
const authGuardScript = `
(function() {
  try {
    var publicPaths = ['/login', '/signup', '/forgot-password', '/reset-password'];
    var path = window.location.pathname;
    var isPublic = path === '/' || publicPaths.some(function(p) {
      return path === p || path.indexOf(p + '/') === 0;
    });
    if (!isPublic && !localStorage.getItem('gunivox.jwt')) {
      var dest = '/login?next=' + encodeURIComponent(path + window.location.search);
      window.location.replace(dest);
    }
  } catch (e) {}
})();`;
```

### 2. Mounted-gate for password-manager hydration
Auth pages skip SSR rendering of the form to avoid LastPass/1Password
icon injection causing hydration mismatches:

```tsx
const [mounted, setMounted] = useState(false);
useEffect(() => setMounted(true), []);
if (!mounted) return <div className="card p-6 flex justify-center"><Loader2 className="animate-spin" /></div>;
// real form below
```

### 3. `<RequireAdmin>` wrapper
```tsx
export function RequireAdmin({ children }) {
  const { isAdmin, loading } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (!loading && !isAdmin) router.replace("/dashboard");
  }, [isAdmin, loading, router]);
  if (loading || !isAdmin) return null;
  return <>{children}</>;
}
```

### 4. Sidebar role filter
```tsx
const NAV = [
  { href: "/dashboard", label: "Dashboard", role: "any" },
  { href: "/inbox",     label: "Inbox",     role: "any" },
  { href: "/team",      label: "Team",      role: "admin" },
  // ...
];

{NAV.map((item) => {
  if (item.role === "admin" && !isAdmin) return null;
  return <Link ... />;
})}
```

### 5. Atomic lock acquire via 409
Backend returns HTTP 409 with `{detail: {locked_by_name, locked_until_at}}`
when another counsellor holds the lock. Frontend catches:

```ts
try {
  const lock = await api.post(`/api/leads/${id}/lock`, {});
  setActive({ card, lock });
} catch (e) {
  if (e instanceof ApiError && e.status === 409) {
    toast.error(`Locked by ${e.body.detail.locked_by_name}`);
  }
}
```

### 6. Note-stanza parsing
Notes are stored as a single concatenated string with markers:
```
[YYYY-MM-DD HH:MM UTC] {user_name}:
{note text}

[YYYY-MM-DD HH:MM UTC] {user_name}:
{more text}
```

Frontend parser splits this back into individual entries for timeline
display:

```ts
function parseNotesBlob(raw: string | null): ParsedNote[] {
  const header = /\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC\]\s*([^\n:]+):/g;
  // ... walk the regex matches, extract body text between headers
}
```

### 7. Lazy transcript fetching
Long-form data (call transcripts) only fetched when the user expands
that timeline row. Cached in a `Map<number, CallDetail | "loading" | "error">`
so re-expanding the same row doesn't re-fetch.

### 8. Date-mode history vs live
Pages with a date filter (Inbox, Pipeline, Analytics) **disable
auto-refresh** when a custom date range is active — historical data
doesn't change, so polling wastes bandwidth. Auto-refresh resumes when
filters are cleared.

---

## Theme tokens

Custom Tailwind colors mapped in `tailwind.config.ts` and CSS variables
in `globals.css`:

| Token | Light | Dark | Used for |
|---|---|---|---|
| `bg-bg` | white | near-black | Page background |
| `bg-surface` | gray-50 | gray-900 | Card backgrounds |
| `border-border` | gray-200 | gray-800 | Hairline borders |
| `text-ink` | gray-900 | gray-100 | Primary text |
| `text-ink-dim` | gray-600 | gray-400 | Secondary text |
| `text-ink-faint` | gray-400 | gray-500 | Tertiary text |
| `text-brand` | red-600 | red-400 | Accent (button, active states) |
| `text-danger` | red-500 | red-400 | Errors |
| `bg-hover` | gray-100 | gray-800/50 | Hover backgrounds |

Theme toggled via `lib/theme-context.tsx`, persists in localStorage,
applied via a `dark` class on `<html>`.

---

## Build + deploy

```bash
# Dev
npm install
npm run dev               # http://localhost:3000

# Type-check
npm run typecheck         # tsc --noEmit

# Production build
NEXT_PUBLIC_API_URL=https://your-api.example.com npm run build
npm start                 # http://0.0.0.0:3000
```

`NEXT_PUBLIC_API_URL` is read at build time and baked into the JS bundle.
Re-set + rebuild if it changes.

---

## What's NOT in the frontend (intentional)

- No state library (no Redux, Zustand, Jotai). Page-local `useState` + context is enough.
- No data-fetching library (no React Query, SWR). `useEffect` + `fetch` is enough.
- No CSS-in-JS (no styled-components, Emotion). Tailwind utilities only.
- No animation library (no Framer Motion). Tailwind transitions only.
- No form library (no React Hook Form). Native `<form>` + `useState` is enough.
- No internationalization (no i18next). Single-locale UI; Gujarati appears only in user-generated content.

These choices keep the bundle small (~100kB First-Load JS shared) and
the mental model tight. Add libraries when you hit a wall, not preemptively.

---

## File layout reference (where to look)

| Need to change... | File |
|---|---|
| Sidebar items | `components/sidebar.tsx` |
| Top bar (logo, system status, theme) | `components/topbar.tsx` |
| Theme colours | `app/globals.css` + `tailwind.config.ts` |
| Auth flow | `lib/auth-context.tsx` + `app/login/page.tsx` |
| Admin-only gate | `components/require-admin.tsx` |
| Disposition options | `app/(app)/inbox/page.tsx` `DISPOSITIONS` constant |
| Timeline icons + labels | `app/(app)/leads/[id]/page.tsx` `EVENT_META` |
| Note parser | `app/(app)/leads/[id]/page.tsx` `parseNotesBlob` |
| API base URL | `.env.local` / `.env.production` `NEXT_PUBLIC_API_URL` |
| Phone masking policy | `lib/phone.ts` |
| Toast styling | `lib/toast-context.tsx` |
| Inbox notification logic | `lib/inbox-notifications.tsx` |

---

## Re-implementation checklist (for a similar app)

If you're building a similar admissions/CRM frontend in another project,
this is the order I'd build in:

1. **Routing skeleton + auth** — public auth pages + `(app)` route group + auth-context + JWT in localStorage + auth-guard inline script
2. **API client** + ApiError class + 401 → logout
3. **Sidebar + topbar + theme toggle** with role filtering
4. **Leads page + Leads detail** — get the CRUD baseline working
5. **Inbox kanban + lock-acquire workflow** — the operational core
6. **Timeline parser + AI transcript expansion** — biggest UX win
7. **Pipeline + Closures** — strategic views, server-bucketed
8. **Analytics** — last, because it depends on the data flowing
9. **Reports** — CSV/PDF downloads with branded headers
10. **Admin Team page + presence** — last_seen_at heartbeat

Get auth + Inbox right and the rest falls out of the same patterns.
