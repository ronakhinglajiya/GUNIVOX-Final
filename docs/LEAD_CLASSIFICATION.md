# Gunivox — Lead Classification (Hot · Warm · Cold)

> Source of truth:
> - Signal collection: [backend/app/telephony/bridge.py](../backend/app/telephony/bridge.py) (~line 679)
> - Classifier: [backend/app/conversation/lead_scoring.py](../backend/app/conversation/lead_scoring.py)
> - Persistence + mirror: [backend/app/db/repo.py](../backend/app/db/repo.py) `finalize_call()`

Two-stage pipeline. Signals are collected in real time during the call.
A single LLM judgment runs at hangup. The result is mirrored from `Call`
onto `Lead` so every page (Inbox, Pipeline, Closures, Analytics) reads
the same value.

---

## Stage 1 — Signal collection (during the call)

Every user utterance is scanned for four sticky boolean flags. "Sticky"
means *once true, stays true* for the rest of the call — so a caller
who asks about fees in turn 3 is still flagged at hangup.

| Flag | Triggers when user text contains (case-insensitive, multilingual) |
|---|---|
| `asked_about_fees` | ફી · fee · ખર્ચ · cost · रुपये · rs · price |
| `asked_about_counsellor` | કાઉન્સ · counsel · professor · પ્રોફેસ · સર · ટીચર · teacher |
| `asked_about_admission` | એડમિશન · admission · પ્રવેશ · apply · ફોર્મ · form |
| `asked_for_callback` | કોલ બેક · callback · call back · ફોન કરો · phone karo · મુલાકાત · visit |

In parallel, `_sync_session_slots()` builds `programs_discussed: list[str]`.
Every time the brain detects a focused program, the program name is
appended. By hangup we know exactly which programs the caller engaged
with.

**This stage is regex-style keyword detection, not LLM** — it's fast,
deterministic, and runs on every turn without burning latency.

---

## Stage 2 — Post-call classification (one LLM call at hangup)

Fires once from `_finalize_call` after the WebSocket closes. Why
post-call instead of mid-call: this classification is for the
**outreach pipeline**, not the in-call agent. Latency doesn't matter
here, and seeing the whole conversation in one shot beats inferring
from partial state.

### What it sees

| Input | Detail |
|---|---|
| `signals` dict | The four booleans above + `call_duration_seconds` + `turn_count` + `caller_name_given` + `programs_discussed[:6]` |
| `transcript` | Last 30 turns of conversation text (capped to keep token cost cheap on multi-minute calls) |

### The model

- `gpt-4o-mini`
- `temperature=0` (deterministic — same transcript always classifies the same way)
- JSON mode (`response_format={"type": "json_object"}`) — model is constrained to return a parseable object, no preamble
- `max_tokens=200` — output is one short JSON blob

### The rubric (encoded in the system prompt; applied in order)

#### 🔥 HOT — at least ONE of:
1. **Specific program + decision-stage question.** Asked about ONE
   particular program AND asked at least one of: fees, counsellor name,
   eligibility, admission process, when classes start.
2. **Direct readiness signal.** Said they want a callback / will visit
   campus / asked to talk to a counsellor.
3. **Contact-readiness combo.** Gave their name AND a "please call me
   back" / "send me details" / "when can I come" signal.

> *Intuition:* hot leads have moved past *browsing* into *evaluating a
> specific option*. They're asking the questions you ask right before
> signing up.

#### 🌡️ WARM:
1. **Browsing mode.** Asked about **2+ different programs** without
   diving deep into any single one.
2. **Sustained engagement.** Engaged for **4+ turns** with on-topic
   questions but no clear buying signal yet.
3. **Identified but not committed.** Gave their name AND asked at least
   one substantive program question (but didn't ask about
   fees/counsellor/admission process).

> *Intuition:* warm leads are *exploring* — interested enough to spend
> time, not yet ready to commit.

#### ❄️ COLD — at least ONE of:
1. **Drive-by.** Call ended within ~45 seconds with minimal engagement.
2. **Filler-only replies.** Replies were mostly `હા` / `okay` / `hmm`
   with no real questions.
3. **Bounced.** Hung up immediately after the greeting or after the AI's
   first answer.
4. **Explicit no.** Said "no thanks" / "wrong number" / "I'm not
   interested."

### Tie-breaker (important calibration choice)

> *"If ambiguous between two temperatures, pick the cooler one —
> under-promise on lead quality."*

A false hot wastes a counsellor's 5-min lock + drains SLA budget. A
false cold just sits in the cold lane until someone re-runs outreach.
**Under-promising is the cheaper error.**

### Output contract

```json
{
  "temperature": "hot" | "warm" | "cold",
  "program": "B.Tech (Marine)" | null,
  "reasons": "Asked about B.Tech Marine fees and counsellor in 6 turns"
}
```

The `reasons` field is required to be **one concrete sentence under 25
words, pointing at evidence** — not generic ("seemed interested"). This
becomes the `lead_reasons` payload that surfaces in the Lead detail
timeline and the Pipeline cards.

---

## Stage 3 — Persistence + propagation

Once the classifier returns, `repo.finalize_call()` writes:

1. **On the `Call` row**
   - `lead_temperature`, `lead_program`, `lead_reasons`
2. **Mirror onto the `Lead` row**
   - `Lead.lead_temperature` updated to match the *latest* call's
     classification. This is what Inbox/Pipeline/Closures/Analytics all
     read.
3. **Two timeline events appended to `lead_events`**
   - `TEMPERATURE_SET` with payload `{from, to, call_id, program, reasons}`
   - `AI_CALL_ENDED` with payload `{call_id, outcome, duration_s, temperature}`

This is what gives the Inbox SLA badge its "hot for 12 minutes" clock —
time since the most recent `TEMPERATURE_SET` event whose `payload.to =
'hot'`.

---

## Failure modes (where temperature stays NULL)

The system explicitly **does not fabricate** a classification.
`Lead.lead_temperature` is left `NULL` when:

| Failure | Why |
|---|---|
| Zero-turn call | No transcript to classify (`if not transcript: return None`) |
| OpenAI API error | Better unclassified than wrong-classified (`except Exception: log.exception; return None`) |
| Non-JSON response | Logged as warning, discarded |
| `temperature` value not in {hot, warm, cold} | Logged as warning, discarded |

In the live DB, ~91% of leads have `lead_temperature IS NULL` — most
of those are no-pickup calls (busy / no_answer / no_speech) where there
was nothing to classify.

---

## What it deliberately ignores

- **Lead source quality** — a CSV-loaded lead and a webhook lead are
  scored identically; the classifier doesn't know where the lead came
  from.
- **Demographics** — no age/location/parental-income inference; those
  fields aren't fed to the model.
- **Prior call history** — each call is classified independently. If a
  lead was hot last week and cold today, today wins. The mirror logic
  on `Lead.lead_temperature` is *most-recent-call*, not *highest-ever*.
- **Voice sentiment** — no audio analysis, only the transcript text.
  Friendly tone doesn't help; what was said does.

The classification is fundamentally a **pattern match on what the
caller asked about**, plus a length/engagement floor. That keeps it
predictable enough to debug ("why did this go cold? — they only said
okay and hung up") while still smart enough to read intent.

---

## Tuning hooks

If hot-rate looks too low or too high, here are the levers in order of
impact:

| Lever | Where | Effect |
|---|---|---|
| Edit HOT/WARM/COLD rubric clauses | `CLASSIFIER_SYSTEM_PROMPT` in `lead_scoring.py` | Big — changes what crosses the line |
| Add keyword sets to signal detection | `bridge.py` ~line 679 | Medium — more signals available for the LLM |
| Raise/lower turn-count thresholds | rubric (4+ turns for warm, ~45s for cold) | Medium |
| Add a "reasons" cross-check before trusting the temperature | New post-classifier validator | Low — only matters at the margin |
| Change model | `CLASSIFIER_MODEL` constant | Variable — gpt-4o gives ~5% better recall, 4× cost |

Counsellors will eventually be able to manually override AI temperature
via the disposition modal (`next_temperature` field already wired in
`inbox.py`). Those overrides will become training data for the
classifier — they're already logged as `TEMPERATURE_OVERRIDDEN` events.
