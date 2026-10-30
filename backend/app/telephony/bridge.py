"""Vobiz media-stream bridge.

Wire protocol (Plivo-compatible <Stream>):

  Incoming events (from Vobiz to us):
    {"event":"start","start":{"streamId":"...","callId":"...","mediaFormat":{...}}}
    {"event":"media","media":{"timestamp":"...","payload":"<base64 PCM L16>","track":"inbound"}}
    {"event":"stop","stop":{"streamId":"..."}}

  Outgoing events (from us to Vobiz):
    {"event":"playAudio","media":{"contentType":"audio/x-l16","sampleRate":16000,"payload":"<base64 PCM>"}}
    {"event":"checkpoint","name":"..."}        # optional, for sync
    {"event":"clearAudio"}                     # interrupt current playback

Pipeline per call:
  1. Vobiz sends 'start' -> we create a CallSession + Sarvam Saarika streaming WS.
  2. Vobiz sends 'media' frames -> bridge pushes PCM bytes into Sarvam STT.
  3. Sarvam emits a final transcript -> bridge invokes BrainEngine.turn().
  4. BrainEngine yields (text, wav_audio) chunks -> bridge strips WAV header,
     base64-encodes, sends as playAudio events.
  5. Vobiz sends 'stop' -> bridge closes Sarvam WS, persists call summary
     (Phase 4 will add DB writes; for now we just log).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.brain.engine import BrainEngine
from app.brain.fillers import FillerLibrary
from app.brain.llm import LLM
from app.brain.rag import RAGEngine
from app.brain.tts import StreamingTTS, StreamingTTSSession
from app.clients import get_http_client, get_openai_client
from app.config import get_settings
from app.conversation import TurnHistory
from app.db import repo
from app.db.session import SessionLocal
from app.events import get_event_bus
from app.stt.sarvam import SarvamSTT
from app.stt.vad import AudioSegmenter, _max_abs
from app.telephony.audio_utils import (
    chunk_pcm,
    pcm_to_b64,
    pcm_to_wav,
    strip_wav_header,
)

log = logging.getLogger("gunivox.bridge")
router = APIRouter(tags=["vobiz"])

# Grace period before barge-in detector arms after AI audio starts. Lets the
# phone's echo canceller adapt so our outgoing audio's echo doesn't trip
# barge-in. Same value referenced inline at all AI-audio entry points.
_BARGE_IN_GRACE_S = 1.0

# Dead-silence watchdog (2026-05-21). If the caller is completely silent
# for this many seconds AFTER the agent finishes a turn, play a goodbye
# announcement and end the call. Saves the caller hanging on a dead line
# and saves us minutes of paid call time on lines that have gone away.
DEAD_SILENCE_HANGUP_S = 20.0
# Two-stage dead-silence flow (2026-05-29):
#   1. SILENCE_CHECK_IN_S of caller silence (after agent finished talking) →
#      play "શું તમે કોલ પર છો?" to nudge them.
#   2. SILENCE_END_AFTER_CHECK_IN_S more seconds of silence after the check-in
#      finishes playing → play "કોઈ જવાબ ના મળવાને કારણે અમે કોલને એન્ડ
#      કરી રહ્યા છીએ" and hang up.
#   If the caller speaks at any point, both timers reset.
SILENCE_CHECK_IN_S = 10.0
SILENCE_END_AFTER_CHECK_IN_S = 3.0

_brain_engine: BrainEngine | None = None
_fillers: FillerLibrary | None = None


def get_brain() -> BrainEngine:
    global _brain_engine
    if _brain_engine is None:
        s = get_settings()
        oai = get_openai_client()
        rag = RAGEngine(s.faiss_index_path, s.metadata_mapping_path, oai)
        llm = LLM(oai, s.openai_model, s.system_prompt)
        tts_stream = StreamingTTS(
            api_key=s.sarvam_api_key,
            base_url=s.sarvam_base_url,
            model=s.sarvam_tts_model,
            speaker=s.sarvam_tts_speaker,
            sample_rate=16000,
        )
        _brain_engine = BrainEngine(rag, llm, tts_stream)
    return _brain_engine


def get_fillers() -> FillerLibrary:
    global _fillers
    if _fillers is None:
        s = get_settings()
        _fillers = FillerLibrary(s.recordings_dir.parent / "data" / "fillers")
        _fillers.warm_load()
    return _fillers


# Known Sarvam STT mishears in admissions context. Map: regex pattern →
# replacement. Each entry was confirmed from production logs where the
# wrong token has NO admissions meaning but is acoustically close to a
# real admissions word. Applied case-insensitively for English/Latin
# tokens, exact for Gujarati script.
import re as _re

_STT_REPAIRS: tuple[tuple["_re.Pattern[str]", str, str], ...] = (
    # "હોસ્ટેલ" → "તેલ" (Sarvam drops 'ho' onset → 'stel' → 'તેલ').
    # Match as a standalone word OR a short utterance fragment.
    (_re.compile(r"\bતેલ\b"), "હોસ્ટેલ", "hostel-mishear"),
    (_re.compile(r"\bતેલ\.?\s*$"), "હોસ્ટેલ?", "hostel-mishear-eos"),
    (_re.compile(r"\bતેલ મળશે\b"), "હોસ્ટેલ મળશે?", "hostel-mishear-phrase"),
    (_re.compile(r"\bઓસ્ટેલ\b"), "હોસ્ટેલ", "hostel-mishear-ostel"),
    (_re.compile(r"\bhostal\b", _re.IGNORECASE), "hostel", "hostel-typo"),
    # Other common admissions-context mishears (additive, low-risk):
    # "સ્કોલરશિપ" sometimes heard as "સ્કોલરશીપ" / "સ્કૉલરશિપ" — those are
    # spelling variants that already work, no repair needed.
)


def _repair_stt_mishears(text: str) -> str:
    """Apply the _STT_REPAIRS map. Returns repaired text + logs any
    repair that fired so we can audit later."""
    if not text:
        return text
    repaired = text
    for pat, repl, label in _STT_REPAIRS:
        new = pat.sub(repl, repaired)
        if new != repaired:
            log.info("STT repair %s: %r -> %r", label, repaired, new)
            repaired = new
    return repaired


def _is_degenerate_repetition(text: str) -> bool:
    """True if the transcript is just one short token repeated many times.

    Sarvam STT can latch onto rhythmic noise / agent-echo / phone hum and
    output things like "હા હા હા હા હા હા હા હા હા ..." (~100x). These
    aren't real caller utterances and should be dropped before they pollute
    the turn-counter, the LLM context, and the dialog state.

    Heuristic: 8+ whitespace tokens, the most-common token is ≤3 chars,
    and that token accounts for ≥80% of all tokens.
    """
    if not text:
        return False
    tokens = text.split()
    if len(tokens) < 8:
        return False
    from collections import Counter
    counts = Counter(tokens)
    top_tok, top_n = counts.most_common(1)[0]
    if len(top_tok) > 3:
        return False
    return top_n / len(tokens) >= 0.8


async def _handle_handoff(
    session: "CallSession",
    language: str,
    ws,  # WebSocket
    brain,
    tts_session,
) -> None:
    """User asked to talk to a counsellor. Play a transition filler and trigger
    the warm transfer. If no counsellor available, fall back to scheduling a
    callback (handled by an LLM-driven turn)."""
    if session.db_call_id is None:
        log.warning("handoff requested but no db_call_id — cannot transfer")
        return
    from app.handoff import HandoffError, NoCounsellorAvailable, transfer_to_counsellor

    fillers = get_fillers()
    fb = fillers.get("let_me_check", language)
    if fb:
        try:
            await send_audio(ws, fb)
        except Exception:
            log.exception("filler send failed")

    try:
        async with SessionLocal() as db:
            result = await transfer_to_counsellor(db, session.db_call_id)
            await db.commit()
            log.info(
                "handoff complete: call=%d → counsellor=%d room=%s",
                result.call_id, result.counsellor_id, result.conference_room,
            )
    except NoCounsellorAvailable:
        log.info("no counsellor available; falling back to callback offer")
        # The next LLM turn will offer to schedule a callback (user is still on
        # the line). For now we just let them re-speak and the LLM handles it.
        await brain.tts_stream  # noqa: placeholder, nothing to do here
    except HandoffError:
        log.exception("handoff failed")


async def _play_filler_and_finish(ws, kind: str, language: str) -> None:
    """Play a pre-rendered filler (e.g., goodbye) and let the user hang up."""
    fillers = get_fillers()
    audio = fillers.get(kind, language)
    if audio:
        try:
            await send_audio(ws, audio)
        except Exception:
            log.exception("finish filler send failed")


async def _lookup_or_create_call(vobiz_call_id: str) -> int | None:
    """Find the Call row pre-created by /api/calls/originate.
    Returns call.id or None if no matching Call exists (inbound call with no
    pre-record; we don't auto-create one without a known lead).
    """
    async with SessionLocal() as db:
        call = await repo.get_call_by_vobiz_id(db, vobiz_call_id)
        return call.id if call else None


async def _save_turn(
    call_id: int | None,
    turn_index: int,
    role: str,
    text: str | None,
    latency_ms: int | None = None,
) -> None:
    if call_id is None or not text:
        return
    try:
        async with SessionLocal() as db:
            await repo.add_turn(
                db,
                call_id=call_id,
                turn_index=turn_index,
                role=role,
                text=text,
                latency_ms=latency_ms,
            )
            await db.commit()
        get_event_bus().publish(
            "call.turn",
            call_id=call_id,
            turn_index=turn_index,
            role=role,
            text=text,
            latency_ms=latency_ms,
        )
    except Exception:
        log.exception("save_turn failed")


async def _finalize_call(
    call_id: int | None,
    duration_s: float,
    recording_path: str | None,
    ttfa_samples: list[int],
    session: "CallSession | None" = None,
    outcome: str = "completed",
) -> None:
    if call_id is None:
        return
    ttfa_p50 = _p50(ttfa_samples)

    # Run the lead-temperature classifier on the full transcript. Skip if we
    # don't have a CallSession (defensive) or if the transcript is empty
    # (caller hung up before any STT result).
    lead_temperature: str | None = None
    lead_program: str | None = None
    lead_reasons: str | None = None
    caller_name: str | None = None
    if session is not None and session.history.turns:
        caller_name = session.caller_name
        # Defensive fallback: if per-turn sync never resolved a name (e.g. the
        # caller gave it then hung up before another turn ran, or a turn was
        # cancelled mid-stream), scan the transcript one more time at finalize
        # so we don't lose a name the caller actually spoke.
        if not caller_name:
            for t in session.history.turns:
                if t.role != "user":
                    continue
                nm = _extract_caller_name(t.text or "")
                if nm:
                    caller_name = nm
                    session.caller_name = nm
                    log.info(
                        "call %s caller_name resolved at finalize: %r",
                        session.call_id, nm,
                    )
                    break
        try:
            from app.clients import get_openai_client
            from app.conversation.lead_scoring import classify_lead

            transcript = session.history.to_openai_messages(max_turns=60)
            score = await classify_lead(
                get_openai_client(),
                transcript,
                duration_s=duration_s,
                programs_discussed=session.programs_discussed,
                caller_name=session.caller_name,
                asked_about_fees=session.asked_about_fees,
                asked_about_counsellor=session.asked_about_counsellor,
                asked_about_admission=session.asked_about_admission,
                asked_for_callback=session.asked_for_callback,
            )
            if score is not None:
                lead_temperature = score.temperature
                lead_program = score.program or (
                    session.programs_discussed[0] if session.programs_discussed else None
                )
                lead_reasons = score.reasons
        except Exception:
            log.exception("lead classifier failed (continuing)")

    try:
        async with SessionLocal() as db:
            await repo.finalize_call(
                db,
                call_id,
                duration_s=duration_s,
                outcome=outcome,
                recording_path=recording_path,
                ttfa_p50_ms=ttfa_p50,
                lead_temperature=lead_temperature,
                lead_program=lead_program,
                lead_reasons=lead_reasons,
                caller_name=caller_name,
            )
            # Settle the campaign_lead that was marked in_progress when the
            # dialer picked this lead up. Without this the row stays at
            # in_progress forever — the Campaigns page shows X stuck
            # in-progress, 0 done, and the dialer can't re-pick the same
            # campaign-lead (its eligibility filter excludes in_progress).
            # 2026-05-28: was previously "future work" per a comment in
            # dialer.py; wiring it in now.
            try:
                from app.db import campaigns_repo as _crepo
                call_row = await repo.get_call(db, call_id)
                if call_row is not None and call_row.lead_id is not None:
                    cl = await _crepo.find_in_progress_for_lead(db, call_row.lead_id)
                    if cl is not None:
                        # Duration heuristic: < 5s is a no-answer / rejected
                        # before pickup → failed_retryable so the retry policy
                        # gets another shot. Anything longer = real call → done.
                        new_status = "failed_retryable" if duration_s < 5.0 else "done"
                        await _crepo.mark_attempt(
                            db, cl.id, new_status=new_status,
                            increment_attempts=False,  # already incremented at dial-pickup
                        )
                        log.info(
                            "campaign_lead %d -> %s (call_id=%d duration=%.1fs)",
                            cl.id, new_status, call_id, duration_s,
                        )
            except Exception:
                log.exception("campaign_lead settle failed (continuing)")
            await db.commit()
            # Kick the dialer immediately so the NEXT queued lead starts
            # right away instead of waiting up to dialer_tick_seconds. With
            # max_concurrent=1 (strict one-by-one mode) this is the
            # difference between calls back-to-back vs a 5s gap each turn.
            try:
                from app.campaigns import get_dialer
                d = get_dialer()
                if d is not None:
                    asyncio.create_task(d._tick())
            except Exception:
                log.exception("dialer kick-on-call-end failed (continuing)")
        get_event_bus().publish(
            "call.ended",
            call_id=call_id,
            duration_s=duration_s,
            outcome=outcome,
            ttfa_p50_ms=ttfa_p50,
            recording_path=recording_path,
            lead_temperature=lead_temperature,
            lead_program=lead_program,
            lead_reasons=lead_reasons,
            caller_name=caller_name,
        )
    except Exception:
        log.exception("finalize_call failed")


@dataclass
class CallSession:
    call_id: str
    db_call_id: int | None = None
    stream_id: str = ""
    language: str = "gu"
    history: TurnHistory = field(default_factory=lambda: TurnHistory(call_id=""))
    inbound_pcm: bytearray = field(default_factory=bytearray)
    # Streaming write target for the inbound recording. PCM bytes get appended
    # to a `.pcm` file on disk as they arrive so the audio survives even a
    # hard kill of the process; the WAV header is bolted on at hangup.
    recording_path: "Path | None" = None
    recording_file: "Any | None" = None
    recording_bytes: int = 0
    started_at: float = field(default_factory=time.time)
    # Timestamp of the FIRST inbound media frame from Vobiz. This is when
    # the callee actually picked up — `started_at` runs from WS-connect, but
    # Vobiz opens the WS as soon as it starts dialling, so the gap between
    # `started_at` and `first_media_at` is the ringing time (~5-15s). Used
    # by billing so we charge only for actual talk time, not ring time.
    first_media_at: float | None = None
    turn_count: int = 0
    ttfa_samples: list[int] = field(default_factory=list)
    audio_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    # While AI is playing audio, mute STT so the AI's own voice (bleeding back
    # via phone speaker → mic) doesn't get re-transcribed and loop.
    ai_speaking_until: float = 0.0
    # Barge-in grace period: don't fire barge-in for the first ~1.5s of any new
    # AI speech episode. Gives the phone's echo canceller time to adapt before
    # we'd otherwise mistake the AI's own echo for a loud user interruption.
    barge_in_armed_at: float = 0.0
    # Dead-silence watchdog (2026-05-21). Timestamp of the last sign of life
    # from the caller (VAD speech-start OR final transcript). The watchdog
    # task fires the goodbye + hangup when this gets stale beyond
    # DEAD_SILENCE_HANGUP_S seconds AFTER the last AI speech finished.
    last_user_speech_at: float = field(default_factory=time.time)
    # ── Conversation slots, persistent across history truncation ───────────
    # Caller's name as extracted on turn 1. Never re-asked once set.
    caller_name: str | None = None
    # Program the conversation is currently focused on (full JSON record).
    # Set when an assistant reply names a program; preserved across filler
    # turns ("હમ", "ઓકે") so follow-up questions ("counsellor name?", "ફી?")
    # always retrieve THAT program's data, not whatever the embedding
    # happens to match on a tiny query.
    focused_program: dict | None = None
    # Programs the user has expressed interest in across the whole call
    # (used by the post-call lead-temperature classifier).
    programs_discussed: list[str] = field(default_factory=list)
    # Lead-flow signals collected during the call.
    asked_about_fees: bool = False
    asked_about_counsellor: bool = False
    asked_about_admission: bool = False
    asked_for_callback: bool = False

    def reset_buffer(self) -> None:
        self.inbound_pcm.clear()


@router.websocket("/vobiz/stream/{call_id}")
async def vobiz_stream(ws: WebSocket, call_id: str) -> None:
    await ws.accept()
    session = CallSession(call_id=call_id, history=TurnHistory(call_id=call_id))
    # Link to the pre-created Call row from /api/calls/originate so we can
    # persist Turns + finalize on hangup.
    session.db_call_id = await _lookup_or_create_call(call_id)
    settings = get_settings()
    brain = get_brain()
    fillers = get_fillers()

    log.info(
        "vobiz call %s connected (db_call_id=%s)", call_id, session.db_call_id
    )

    # Open a streaming PCM file. We append raw PCM bytes as they arrive so the
    # recording survives even if the process is killed (SIGKILL etc). On
    # hangup we slap a WAV header on and rename. If the process dies, the
    # .pcm file alone is recoverable with any standard tool.
    try:
        settings.recordings_dir.mkdir(parents=True, exist_ok=True)
        session.recording_path = settings.recordings_dir / f"{call_id}.pcm"
        session.recording_file = open(session.recording_path, "wb")
    except Exception:
        log.exception("could not open recording file (recording will be skipped)")
        session.recording_file = None

    # Sarvam streaming STT is unreliable (returns no events mid-call); we use
    # batch HTTP STT instead, fed by a local amplitude-based VAD segmenter.
    stt = SarvamSTT(
        get_http_client(),
        api_key=settings.sarvam_api_key,
        base_url=settings.sarvam_base_url.replace("wss://", "https://"),
        model=settings.sarvam_stt_model,
    )
    segmenter = AudioSegmenter()

    # Pre-open a TTS session WS that all turns will reuse.
    tts_session = brain.tts_stream.session(language=session.language)
    tts_connect_task = asyncio.create_task(tts_session.connect())

    utterance_q: asyncio.Queue[bytes | None] = asyncio.Queue()

    stt_task: asyncio.Task | None = None
    current_turn_task: asyncio.Task | None = None
    greet_task: asyncio.Task | None = None
    greet_end_ts: float = 0.0

    # Keywords identifying Vobiz's automatic "call is being recorded" announcement
    # so we can drop it while still allowing the user to speak over the greet.
    RECORDING_KEYWORDS = ("રેકોર્ડ", "record", "रिकॉर्ड")

    # During AI speech we don't feed STT (echo would loop), but we still watch
    # amplitude. Sustained loud audio = real user trying to interrupt.
    # Thresholds tuned against phone-speaker echo of our own TTS (Bulbul v2 at
    # 16k linear16). Echo peaks ~5000–6500; real user voice peaks ~7000+.
    # Round 16 (2026-05-21): user reported "I'm not getting interruption" —
    # phone audio gain varies a lot, the old 6500 threshold was missing real
    # interruptions. Lowered to 5000 + sustained 300ms (was 500ms) so a
    # short forceful "stop!" or "wait!" cancels AI audio promptly.
    # Round 17 (2026-05-26 demo-eve): 5000 sits inside the echo band — on
    # speakerphone callers it false-fires on the agent's own voice bleeding
    # back, cancels the reply mid-sentence, and the caller hears nothing.
    # Raised to 8000 (above echo, below real voice). 300ms sustained still
    # catches an intentional "wait!" / "stop!".
    BARGE_IN_AMP = 8000
    BARGE_IN_MIN_MS = 300
    loud_run_ms = 0

    async def cancel_current_audio(reason: str) -> None:
        """Cancel any in-flight TTS playback (greet or turn) and tell Vobiz to flush."""
        nonlocal current_turn_task, greet_task
        cancelled = False
        if greet_task and not greet_task.done():
            log.info("cancelling greet playback (%s)", reason)
            greet_task.cancel()
            cancelled = True
        if current_turn_task and not current_turn_task.done():
            log.info("BARGE-IN (%s): cancelling turn %d", reason, session.turn_count)
            current_turn_task.cancel()
            cancelled = True
        if cancelled:
            await send_clear_audio(ws, session.stream_id)
            for t in (greet_task, current_turn_task):
                if t is not None:
                    try:
                        await t
                    except BaseException:
                        pass

    def on_local_speech_start() -> None:
        """Our local VAD detected the user started speaking — barge in."""
        # Reset the dead-silence watchdog: the caller is alive.
        session.last_user_speech_at = time.time()
        if time.time() < greet_end_ts:
            return
        if current_turn_task and not current_turn_task.done():
            log.info("local VAD: speech started, scheduling barge-in")
            asyncio.create_task(cancel_current_audio("local_vad"))

    async def consume_utterances() -> None:
        """Read complete utterances from the queue, batch-transcribe, run turns."""
        nonlocal current_turn_task
        while True:
            pcm = await utterance_q.get()
            if pcm is None:
                return
            try:
                wav = pcm_to_wav(pcm, sample_rate=16000, channels=1)
                t0 = time.perf_counter()
                result = await stt.transcribe(wav, language=session.language)
                stt_ms = int((time.perf_counter() - t0) * 1000)
                text = result.text.strip()
                if not text:
                    continue
                # Caller said something coherent — reset dead-silence watchdog.
                session.last_user_speech_at = time.time()
                lower = text.lower()
                if time.time() < greet_end_ts and any(
                    kw in lower for kw in RECORDING_KEYWORDS
                ):
                    log.info("Vobiz announcement filtered: %r", text)
                    continue
                lang = result.language or session.language
                lang_short = lang.split("-")[0] if "-" in lang else lang
                # STT-mishear repair (2026-05-21). Sarvam confuses certain
                # admissions-context tokens. Most-seen: "હોસ્ટેલ" → "તેલ"
                # (oil — meaningless in admissions, but lex-similar after
                # the 'ho' onset gets dropped). Treating these as the
                # likely intended token before signal/intent logic stops
                # the agent from inferring price_shock from nonsense.
                text = _repair_stt_mishears(text)
                if _is_degenerate_repetition(text):
                    log.info(
                        "transcript DROPPED (degenerate repetition) [%s, %dms]: %s",
                        lang, stt_ms, text[:80],
                    )
                    continue
                log.info("transcript [%s, %dms]: %s", lang, stt_ms, text)

                await cancel_current_audio("new_transcript")
                turn_index_user = session.turn_count * 2  # user=even, ai=odd
                asyncio.create_task(
                    _save_turn(session.db_call_id, turn_index_user, "user", text)
                )

                # Intent detection: handoff / callback / goodbye get
                # explicit handling; info_query goes to the LLM.
                from app.conversation import detect_intent

                intent = detect_intent(text)
                if intent.intent == "handoff":
                    log.info("intent=handoff (kw=%r) — triggering transfer", intent.matched_keyword)
                    asyncio.create_task(
                        _handle_handoff(session, lang_short, ws, brain, tts_session)
                    )
                    continue
                if intent.intent == "goodbye":
                    log.info("intent=goodbye (kw=%r)", intent.matched_keyword)
                    asyncio.create_task(
                        _play_filler_and_finish(ws, "goodbye", lang_short)
                    )
                    continue
                # 'callback' and 'info_query' both go through the LLM;
                # callback handling is left to the LLM since it needs to ask
                # for time/preferred-program.

                # === Filler / skip_rag decision ===
                # Closing intent detection (thank-yous, byes). When the caller
                # is wrapping up, the "checking" filler is robotic — they
                # don't need any masking, just a fast natural goodbye reply
                # from the LLM. We pass thinking=None (no prelude) and
                # skip_rag=True (no admissions context needed for "you're
                # welcome"). Captured from user feedback 2026-05-20:
                # "when i say thank you it should not say hu joine kau".
                low = text.lower()
                _closing_kws = (
                    "thank you", "thanks", "thanku", "thnk", "thank u",
                    "થેન્ક", "આભાર",      # Gujarati: thank
                    "बाय", "બાય", "bye", "goodbye", "good bye",
                    "ओके बाय", "ઓકે બાય",
                )
                is_closing = any(kw in low for kw in _closing_kws)

                # Short-utterance / slow-speaker guard. If the transcript
                # is one or two words / under ~20 chars, the caller is
                # likely mid-thought, hesitating, or just answered with a
                # filler ("હા", "hmm", "ઓકે", "MS"). Playing a "let me
                # check" reply here sounds robotic — there's nothing to
                # check yet. Skip the prelude entirely so the LLM's quick
                # reply lands without a misleading filler in front.
                stripped_text = text.strip()
                is_short_utterance = (
                    len(stripped_text) < 20
                    and len(stripped_text.split()) <= 2
                )

                if session.turn_count == 0:
                    # Turn-1 fast path: short "નમસ્તે" filler + skip_rag.
                    thinking = fillers.get("name_greet", lang_short)
                    skip_rag = True
                elif is_closing:
                    # Closing turn: no filler, skip RAG. LLM responds with
                    # a quick natural "you're welcome / take care".
                    thinking = None
                    skip_rag = True
                    log.info("call %s closing intent detected — skipping prelude", session.call_id)
                elif is_short_utterance:
                    # Short / hesitating utterance — no robotic filler. The
                    # LLM will respond fast anyway; better to land cleanly.
                    thinking = None
                    skip_rag = False
                    log.info(
                        "call %s short utterance (%r) — skipping prelude",
                        session.call_id, stripped_text,
                    )
                else:
                    thinking = fillers.get_random_thinking(lang_short)
                    skip_rag = False

                # Lead-flow signal detection from the raw user text. These
                # are sticky booleans on the session — once true they stay
                # true so the post-call classifier sees the whole call.
                # (`low` was defined above for closing-intent detection.)
                if any(kw in low for kw in ("ફી", "fee", "ખર્ચ", "cost", "रुपये", "rs", "price")):
                    session.asked_about_fees = True
                if any(kw in low for kw in ("કાઉન્સ", "counsel", "professor", "પ્રોફેસ", "સર", "ટીચર", "teacher")):
                    session.asked_about_counsellor = True
                if any(kw in low for kw in ("એડમિશન", "admission", "પ્રવેશ", "apply", "ફોર્મ", "form")):
                    session.asked_about_admission = True
                if any(kw in low for kw in ("કોલ બેક", "callback", "call back", "ફોન કરો", "phone karo", "મુલાકાત", "visit")):
                    session.asked_for_callback = True

                # SINGLE point of slot sync. Runs after the previous turn's
                # history is committed (engine.py adds the assistant message
                # in its finally, before we get here). Cancellation-safe.
                _sync_session_slots(session, current_user_text=text)

                # Layer 2: compute the conversation-state brief from the
                # full history. Cheap (single linear scan) — happens once
                # per turn before the LLM call. Empty string on turn 1 /
                # before any topics have been covered, which the engine
                # skips injecting.
                from app.conversation import (
                    compute_dialog,
                    compute_proactive,
                    compute_signals,
                    compute_state,
                    render_dialog_for_llm,
                    render_proactive_for_llm,
                    render_signals_for_llm,
                    render_state_for_llm,
                )
                state = compute_state(session.history, user_turn_count=session.turn_count)
                state_brief = render_state_for_llm(state)
                if state_brief:
                    log.info("call %s state brief: %s", session.call_id, state_brief[:200])

                # Layer 3: per-turn intent + emotional signals. Same
                # render-then-inject path as state. NOTE: history already
                # contains the current user turn (engine.turn() did
                # history.add_user before this point on prior turns; for
                # the CURRENT turn the add happens inside engine.turn,
                # AFTER signals are computed — so pass current_user_text
                # explicitly so compute_signals doesn't miss it).
                signals = compute_signals(session.history, current_user_text=text, state=state)
                signals_hint = render_signals_for_llm(signals)
                if signals_hint:
                    log.info(
                        "call %s signals: %s (matched=%s)",
                        session.call_id, signals_hint, signals.matched,
                    )

                # Layer 4: dialog-action recommendation (steer_to_close,
                # ask_qualifier). Same render-then-inject path.
                dialog = compute_dialog(session.history, state, signals, current_user_text=text)
                dialog_hint = render_dialog_for_llm(dialog)
                if dialog_hint:
                    log.info(
                        "call %s dialog: %s (matched=%s)",
                        session.call_id, dialog_hint, dialog.matched,
                    )

                # Layer 5: proactive surfacings (scholarship / hostel /
                # transport / placement). At most ONE per turn. De-duped
                # against told_topics so we never surface the same one twice.
                proactive = compute_proactive(session.history, state, signals, current_user_text=text)
                proactive_hint = render_proactive_for_llm(proactive)
                if proactive_hint:
                    log.info(
                        "call %s proactive: %s (matched=%s)",
                        session.call_id, proactive_hint, proactive.matched,
                    )

                # Family / stream override detection — precedence: a family
                # query (e.g. 'BCA વિશે કહો') takes priority over RAG so
                # the LLM lists variants rather than guessing one. A stream
                # query ('Commerce courses') lists the category instead.
                # When a single specific variant is named (e.g. 'BCA AI ML'),
                # find_family_query returns None and we fall through to the
                # focused_program path.
                from app.brain.catalog import get_catalog
                catalog = get_catalog()
                family_list = catalog.find_family_query(text)
                stream_list = None
                # Campus mention has HIGHEST priority — when caller says
                # "Ahmedabad" / "અમદાવાદ", force the LLM to see the actual
                # list of programs at that campus. Without this, RAG
                # embedding similarity was returning unrelated programs
                # and the agent said "Ahmedabad વિશે માહિતી નથી" even
                # though 18 programs ARE at Ahmedabad.
                campus_list = catalog.find_campus_query(text)
                if family_list is None:
                    stream_list = catalog.find_stream_query(text)
                    # Eligibility pre-guard. If the caller is asking
                    # whether they QUALIFY ('10th પાસ થયો, B.Tech લઈ શકું?'),
                    # the right path is RAG → focused program's eligibility
                    # field, NOT a stream listing. Without this guard the
                    # '10th' substring hit the Diploma stream and the agent
                    # responded with "no info" (2026-05-21 prod bug).
                    if stream_list is not None and _looks_like_eligibility_question(text):
                        log.info(
                            "stream override SUPPRESSED: eligibility question %r",
                            text[:100],
                        )
                        stream_list = None

                # Meta-question routing (Round 7). Caller is asking about
                # the CONVERSATION ITSELF (what did I ask / what's my name
                # / first question). Force skip_rag + drop all overrides
                # + clear focused_program so the LLM gets a clean slate
                # and answers from history.turns alone.
                meta_q = _looks_like_meta_question(text)
                if meta_q:
                    log.info("call %s meta-question %r — skipping RAG/overrides", session.call_id, text[:80])
                    skip_rag = True
                    family_list = None
                    stream_list = None
                    campus_list = None
                    # focused_program is passed through; engine will see
                    # skip_rag=True and skip the RAG branch entirely. The
                    # LLM gets the full conversation history (messages)
                    # + a META_QUESTION_MODE directive (added in engine).
                    meta_focused_override = None  # signal to engine
                else:
                    meta_focused_override = session.focused_program

                current_turn_task = asyncio.create_task(
                    run_turn(
                        ws, brain, session, text, lang_short, tts_session,
                        thinking, skip_rag,
                        session.caller_name, meta_focused_override,
                        family_list, stream_list,
                        state_brief,
                        signals_hint,
                        meta_q,
                        dialog_hint,
                        proactive_hint,
                        campus_list,
                    )
                )
            except Exception:
                log.exception("STT/turn error")

    stt_task = asyncio.create_task(consume_utterances())

    # Dead-silence watchdog (2026-05-21). Ticks every second. When the caller
    # has been quiet for DEAD_SILENCE_HANGUP_S AND the agent has finished its
    # last reply at least 2s ago, play a goodbye announcement and let the
    # send-loop close. Without this the call lingers until Vobiz times out
    # (~30-60s of paid silence).
    dead_silence_fired = False
    async def dead_silence_watchdog() -> None:
        nonlocal dead_silence_fired
        # Track when (if at all) we've already played the check-in prompt
        # this silence episode. Reset to None when the caller next speaks.
        check_in_played_at: float | None = None
        try:
            while True:
                await asyncio.sleep(1.0)
                if dead_silence_fired:
                    return
                now = time.time()
                # Only count silence AFTER agent finished talking (+2s grace
                # for the phone's audio buffer to drain).
                if now < session.ai_speaking_until + 2.0:
                    continue
                # And don't fire before the greet has even finished.
                if now < greet_end_ts + 2.0:
                    continue
                # If the caller spoke AFTER we played the check-in, the
                # episode is over — reset and let the cycle start fresh.
                if (
                    check_in_played_at is not None
                    and session.last_user_speech_at > check_in_played_at
                ):
                    log.info(
                        "call %s caller responded after check-in — resetting silence watchdog",
                        session.call_id,
                    )
                    check_in_played_at = None
                # Silence clock starts from whichever was LATER — user's last
                # speech OR agent's last finished utterance. Fixes a regression
                # where the greet (~7s) + a 3s caller hesitation summed to ≥10s
                # of "idle" measured from session start and fired the goodbye
                # filler ("આભાર, શુભ દિવસ") over the caller's first attempt.
                last_event = max(session.last_user_speech_at, session.ai_speaking_until)
                idle = now - last_event

                # ── Stage 1: 10s of silence → play check-in ──
                if check_in_played_at is None and idle >= SILENCE_CHECK_IN_S:
                    log.info(
                        "silence %.1fs on call %s — playing check-in (\"શું તમે કોલ પર છો?\")",
                        idle, session.call_id,
                    )
                    check_in_played_at = now
                    try:
                        await _play_filler_and_finish(ws, "silence_check_in", session.language)
                        # The check-in audio is ~1.6s; bump ai_speaking_until
                        # so STT stays muted while it plays + a small tail.
                        fillers = get_fillers()
                        cin_audio = fillers.get("silence_check_in", session.language)
                        if cin_audio:
                            cin_secs = max(0, len(cin_audio) - 44) / 32000.0
                            session.ai_speaking_until = max(
                                session.ai_speaking_until, now + cin_secs + 0.5,
                            )
                    except Exception:
                        log.exception("silence check-in send failed")
                    continue

                # ── Stage 2: 3s more silence after check-in → end call ──
                if check_in_played_at is not None and idle >= SILENCE_END_AFTER_CHECK_IN_S:
                    log.info(
                        "no response %.1fs after check-in on call %s — ending call",
                        idle, session.call_id,
                    )
                    dead_silence_fired = True
                    try:
                        await _play_filler_and_finish(ws, "silence_end", session.language)
                    except Exception:
                        log.exception("silence-end announcement failed")
                    # Wait for the announcement audio to actually reach the
                    # caller's phone before we tear the leg down — silence_end
                    # is ~3.8s and we just dumped it into the WS buffer.
                    # Without this, the REST hangup races the audio and the
                    # caller hears the announcement get clipped mid-word.
                    fillers = get_fillers()
                    end_audio = fillers.get("silence_end", session.language)
                    if end_audio:
                        play_secs = max(0, len(end_audio) - 44) / 32000.0
                        await asyncio.sleep(play_secs + 0.4)
                    # Trigger Vobiz REST hangup. The client retries 3x on
                    # transport errors (DNS blip) before giving up.
                    hangup_ok = False
                    if session.db_call_id is not None:
                        try:
                            from app.config import get_settings as _gs
                            from app.telephony.vobiz import VobizClient
                            s = _gs()
                            async with SessionLocal() as db:
                                call = await repo.get_call(db, session.db_call_id)
                                if call and call.vobiz_call_id:
                                    vc = VobizClient(
                                        get_http_client(),
                                        auth_id=s.vobiz_auth_id,
                                        auth_token=s.vobiz_auth_token,
                                    )
                                    await vc.hangup(call.vobiz_call_id)
                                    hangup_ok = True
                        except Exception:
                            log.exception("dead-silence vobiz hangup failed")
                    # Defensive: close our WebSocket end too. If the REST
                    # hangup succeeded, this is redundant (Vobiz drops the
                    # stream anyway). If it failed (DNS outage), closing
                    # the WS at least releases our resources and signals
                    # Vobiz that the stream is done — Vobiz typically drops
                    # the leg shortly after a clean WS close.
                    try:
                        await ws.close()
                        log.info(
                            "call %s ws closed after silence-end (hangup_ok=%s)",
                            session.call_id, hangup_ok,
                        )
                    except Exception:
                        pass
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("dead-silence watchdog crashed")

    watchdog_task = asyncio.create_task(dead_silence_watchdog())

    media_count = 0

    try:
        while True:
            msg = await ws.receive_text()
            data = json.loads(msg)
            event = data.get("event")

            if event == "connected":
                log.info("vobiz call %s connected (protocol=%s)", call_id, data.get("protocol"))

            elif event == "start":
                # Vobiz puts streamId/callId at top level of the start frame.
                session.stream_id = data.get("streamId") or data.get("start", {}).get("streamId", "")
                log.info(
                    "vobiz call %s start stream=%s data=%s",
                    call_id,
                    session.stream_id,
                    {k: v for k, v in data.items() if k != "event"},
                )
                # Send greet at real-time pace. Track the task so we can
                # cancel it if the user barges in mid-greet.
                greet = fillers.get("greet", session.language)
                if greet:
                    greet_pcm_bytes = max(0, len(greet) - 44)
                    greet_seconds = greet_pcm_bytes / (16000 * 2)
                    greet_end_ts = time.time() + greet_seconds + 0.5
                    # Mute STT for the greet's duration so its phone-speaker
                    # echo doesn't get transcribed (and falsely trigger a turn
                    # responding to our own greet). Loud barge-in still works
                    # via the BARGE_IN_AMP path.
                    session.ai_speaking_until = greet_end_ts
                    # Greet-specific barge-in arm: hold barge-in OFF until
                    # the greet itself ends. The carrier (Vobiz) plays a
                    # "call is being recorded" announcement at the very
                    # start of the connection — its audio amplitude trips
                    # the sensitive barge-in detector (5000/300ms) and
                    # cancels our greet before the caller even hears it,
                    # then the call dies. Disarming barge-in for the full
                    # greet window lets both the carrier announcement AND
                    # our greet play through cleanly. Normal short-grace
                    # barge-in resumes for every subsequent AI turn.
                    session.barge_in_armed_at = greet_end_ts

                    async def send_greet():
                        try:
                            log.info("vobiz call %s greet send starting", call_id)
                            await send_audio(ws, greet)
                            log.info("vobiz call %s greet send DONE", call_id)
                        except Exception:
                            log.exception("greet send failed")

                    log.info(
                        "vobiz call %s sending greet (~%.1fs)", call_id, greet_seconds
                    )
                    greet_task = asyncio.create_task(send_greet())

            elif event == "media":
                media_count += 1
                if media_count == 1:
                    log.info("vobiz call %s first media frame received", call_id)
                    # Mark the moment the callee actually picked up — billing
                    # measures duration from here, not from WS connect, so we
                    # don't charge the caller for ring time.
                    if session.first_media_at is None:
                        session.first_media_at = time.time()
                payload_b64 = data.get("media", {}).get("payload")
                if not payload_b64:
                    continue
                pcm = _b64_decode(payload_b64)
                if session.recording_file is not None and pcm:
                    try:
                        session.recording_file.write(pcm)
                        session.recording_bytes += len(pcm)
                    except Exception:
                        log.exception("recording write failed; disabling further writes")
                        try:
                            session.recording_file.close()
                        except Exception:
                            pass
                        session.recording_file = None

                # If AI is currently playing back, mute STT (echo would loop)
                # but watch for sustained loud audio = user really barging in.
                if time.time() < session.ai_speaking_until:
                    now_t = time.time()
                    frame_ms = len(pcm) / 32.0  # 16kHz mono s16le → 32 bytes/ms
                    # Grace window: skip barge-in entirely for the first
                    # BARGE_IN_GRACE_MS of any AI speech episode so the phone's
                    # echo canceller has time to adapt to our outgoing audio.
                    if now_t < session.barge_in_armed_at:
                        loud_run_ms = 0
                        continue
                    if _max_abs(pcm) >= BARGE_IN_AMP:
                        loud_run_ms += frame_ms
                        if loud_run_ms >= BARGE_IN_MIN_MS:
                            log.info(
                                "BARGE-IN: loud audio over echo, cancelling AI (amp>=%d for %dms)",
                                BARGE_IN_AMP, int(loud_run_ms),
                            )
                            asyncio.create_task(cancel_current_audio("loud_audio"))
                            session.ai_speaking_until = 0.0
                            loud_run_ms = 0
                            segmenter.add_frame(pcm)  # feed this frame too
                    else:
                        loud_run_ms = 0
                    continue

                loud_run_ms = 0
                was_speaking = segmenter._utterance is not None
                segmenter.add_frame(pcm)
                now_speaking = segmenter._utterance is not None
                if now_speaking and not was_speaking:
                    on_local_speech_start()
                while segmenter.has_utterance():
                    utt = segmenter.pop_utterance()
                    if utt:
                        await utterance_q.put(utt)

            elif event == "stop":
                log.info("vobiz call %s stop (media_count=%d)", call_id, media_count)
                break

            elif event in ("playedStream", "checkpoint", "dtmf"):
                log.info("vobiz call %s event=%s data=%s", call_id, event, data)

            else:
                log.warning("vobiz call %s unknown event=%s data=%s", call_id, event, data)

    except WebSocketDisconnect:
        log.info("vobiz call %s disconnected", call_id)
    finally:
        await utterance_q.put(None)
        if stt_task:
            stt_task.cancel()
            try:
                await stt_task
            except BaseException:  # CancelledError is BaseException in py3.13
                pass
        # Cancel the dead-silence watchdog (idempotent — fine if already done).
        try:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except BaseException:
                pass
        except NameError:
            pass  # watchdog never spawned (early error)
        # Close the persistent TTS WS
        try:
            if not tts_connect_task.done():
                tts_connect_task.cancel()
            await tts_session.close()
        except BaseException:
            pass

        # Finalize the streaming PCM recording into a WAV (and keep the .pcm
        # as a fallback in case WAV conversion fails). If we get killed
        # before this runs, the .pcm file is still on disk and recoverable.
        recording_path_str: str | None = None
        try:
            if session.recording_file is not None:
                try:
                    session.recording_file.flush()
                    session.recording_file.close()
                except Exception:
                    log.exception("recording flush/close failed")
                session.recording_file = None
            if session.recording_path and session.recording_path.exists() and session.recording_bytes > 0:
                pcm_bytes = session.recording_path.read_bytes()
                wav_path = session.recording_path.with_suffix(".wav")
                wav_path.write_bytes(pcm_to_wav(pcm_bytes))
                try:
                    session.recording_path.unlink()
                except Exception:
                    pass
                recording_path_str = str(wav_path)
                log.info("saved recording: %s (%d bytes)", wav_path, session.recording_bytes)
        except Exception as e:
            log.error("failed to finalize recording: %s", e)
            # Best-effort fallback: report the raw .pcm path if WAV step failed.
            if session.recording_path and session.recording_path.exists():
                recording_path_str = str(session.recording_path)

        # BILLING-ACCURATE duration: time from first inbound media frame
        # (callee picked up) → now. Falls back to WS-connect baseline if no
        # media ever arrived (caller hung up before pickup). Fixes the
        # "~10s of ring time silently added to every call" overcharge.
        ended_at = time.time()
        if session.first_media_at is not None:
            duration = max(0.0, ended_at - session.first_media_at)
        else:
            duration = max(0.0, ended_at - session.started_at)
        log.info(
            "vobiz call %s ended duration=%.1fs turns=%d media_frames=%d ttfa_p50=%s",
            call_id,
            duration,
            session.turn_count,
            media_count,
            _p50(session.ttfa_samples),
        )
        # Persist final Call row update (includes the lead-temperature
        # classifier run; passes the whole session so the classifier has
        # transcript + behavioural signals to score against).
        await _finalize_call(
            session.db_call_id,
            duration_s=duration,
            recording_path=recording_path_str,
            ttfa_samples=session.ttfa_samples,
            session=session,
            outcome="completed" if session.turn_count > 0 else "no_speech",
        )


async def run_turn(
    ws: WebSocket,
    brain: BrainEngine,
    session: CallSession,
    user_text: str,
    language: str,
    tts_session: StreamingTTSSession | None = None,
    prelude: bytes | None = None,
    skip_rag: bool = False,
    caller_name: str | None = None,
    focused_program: dict | None = None,
    family_list: tuple | None = None,
    stream_list: tuple | None = None,
    conversation_state_brief: str | None = None,
    turn_signals_hint: str | None = None,
    meta_question: bool = False,
    turn_dialog_hint: str | None = None,
    turn_proactive_hint: str | None = None,
    campus_list: tuple | None = None,
) -> None:
    """Stream brain audio to Vobiz with a small jitter buffer.

    If `prelude` is provided (a pre-rendered "thinking" filler), play it FIRST
    while brain.turn() runs in a background task. Bridges the ~4s STT+LLM+TTS
    warmup audibly so the user doesn't hang up thinking the line went dead.
    Brain audio is queued and stitches in right after the prelude.
    """
    session.turn_count += 1
    turn_start = time.perf_counter()
    first_audio_at: float | None = None

    # 16kHz mono linear16 → 32000 bytes per second.
    SR_BYTES = 16000 * 2
    # Smaller initial buffer = brain audio reaches the caller ~100ms sooner.
    # Vobiz still buffers downstream, so we don't need 200ms of pre-roll.
    INITIAL_BUFFER_BYTES = int(SR_BYTES * 0.10)  # 100ms before first send
    STEADY_BUFFER_BYTES = int(SR_BYTES * 0.15)   # 150ms per subsequent flush

    # Mute STT for the WHOLE turn — prelude + silent gap + brain audio + tail.
    # Setting this 30s ahead at turn start prevents the prelude→brain gap
    # (where ai_speaking_until briefly expires) from letting the prelude's
    # tail echo + brain audio's start echo merge into a phantom utterance
    # that gets STT'd as our own AI's voice (caused turn-3 echo loop bug).
    # `flush_buf` will rewrite this with a tight (audio_seconds + 0.5) value
    # once real audio is going out; the finally block clears it.
    session.ai_speaking_until = time.time() + 30.0
    # Arm barge-in grace window for this AI episode.
    session.barge_in_armed_at = time.time() + _BARGE_IN_GRACE_S

    prelude_seconds = 0.0
    if prelude:
        pre_pcm, pre_sr, _ = strip_wav_header(prelude)
        prelude_seconds = len(pre_pcm) / (pre_sr * 2.0) if pre_sr else 0.0

    # Kick brain off in the background so STT+LLM+TTS warmup overlaps with
    # prelude playback. Audio chunks land in this queue.
    brain_q: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def fetch_brain() -> None:
        try:
            async for chunk in brain.turn(
                user_text,
                session.history,
                language=language,
                tts_session=tts_session,
                skip_rag=skip_rag,
                caller_name=caller_name,
                focused_program=focused_program,
                family_list=family_list,
                stream_list=stream_list,
                conversation_state_brief=conversation_state_brief,
                turn_signals_hint=turn_signals_hint,
                meta_question=meta_question,
                turn_dialog_hint=turn_dialog_hint,
                turn_proactive_hint=turn_proactive_hint,
                campus_list=campus_list,
            ):
                if chunk.audio:
                    await brain_q.put(chunk.audio)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("brain.turn failed")
        finally:
            await brain_q.put(None)

    fetch_task = asyncio.create_task(fetch_brain())

    buf = bytearray()
    sent_first = False

    async def flush_buf() -> None:
        nonlocal first_audio_at
        if not buf:
            return
        if first_audio_at is None:
            first_audio_at = time.perf_counter()
            ttfa_ms = int((first_audio_at - turn_start) * 1000)
            session.ttfa_samples.append(ttfa_ms)
            log.info("turn %d brain-TTFA=%dms", session.turn_count, ttfa_ms)
            # Re-arm the barge-in grace window for the brain audio episode.
            # Even with a prelude, the silent gap between prelude end and
            # brain audio start means the phone's echo canceller has
            # nothing to adapt to during the gap — so when brain audio
            # finally starts, its echo can trip barge-in unless we re-grace.
            session.barge_in_armed_at = max(
                session.barge_in_armed_at,
                time.time() + _BARGE_IN_GRACE_S,
            )
        # Extend the AI-speaking window so STT stays muted while this audio plays.
        audio_seconds = len(buf) / 32000.0
        now = time.time()
        base = max(now, session.ai_speaking_until)
        session.ai_speaking_until = base + audio_seconds + 0.5
        await send_audio(ws, bytes(buf))
        buf.clear()

    try:
        if prelude:
            try:
                log.info("turn %d prelude playing (~%.2fs)", session.turn_count, prelude_seconds)
                await send_audio(ws, prelude)
            except Exception:
                log.exception("prelude send failed")

        while True:
            audio = await brain_q.get()
            if audio is None:
                break
            buf.extend(audio)
            threshold = STEADY_BUFFER_BYTES if sent_first else INITIAL_BUFFER_BYTES
            if len(buf) >= threshold:
                await flush_buf()
                sent_first = True

        await flush_buf()
    finally:
        if not fetch_task.done():
            fetch_task.cancel()
            try:
                await fetch_task
            except BaseException:
                pass
        # Clamp ai_speaking_until back to a short tail past the last flush so
        # STT comes back online for the next user turn. Use whichever is
        # smaller of (current value, now + audio_tail) — we don't want to
        # extend the mute when collapsing it.
        now = time.time()
        # 0.5s tail: phone is still playing buffered audio after our last send.
        tail_cap = now + 0.5
        if session.ai_speaking_until > tail_cap:
            session.ai_speaking_until = tail_cap

    # Persist assistant's full response from TurnHistory (engine wrote it).
    # NOTE: slot updates (caller_name, focused_program) are NOT done here.
    # If this turn gets cancelled by a barge-in / new transcript, the post-
    # try/finally code never runs and slot state goes stale. Slot sync now
    # lives in `_sync_session_slots`, called once at the top of every
    # consume_utterances iteration — it's idempotent and reads from history
    # which engine.py updates even on cancellation.
    if session.history.turns:
        last = session.history.turns[-1]
        if last.role == "assistant" and last.text:
            turn_index_ai = (session.turn_count - 1) * 2 + 1
            total_ms = int((time.perf_counter() - turn_start) * 1000)
            asyncio.create_task(
                _save_turn(
                    session.db_call_id, turn_index_ai, "assistant", last.text, total_ms
                )
            )


def _is_ws_closed_error(exc: BaseException) -> bool:
    """Return True if the exception means the WS is gone (caller hung up etc).

    We treat these as expected and exit the send loop quietly. Anything else
    propagates so we keep visibility into real bugs.
    """
    if isinstance(exc, WebSocketDisconnect):
        return True
    if isinstance(exc, RuntimeError):
        # ASGI raises this when we try to send after the socket was closed.
        msg = str(exc)
        return "websocket.send" in msg or "websocket.close" in msg or "response already completed" in msg
    return False


async def send_clear_audio(ws: WebSocket, stream_id: str) -> None:
    """Tell Vobiz to immediately stop playing any audio we sent (for barge-in)."""
    try:
        await ws.send_text(json.dumps({"event": "clearAudio", "streamId": stream_id}))
    except BaseException as e:
        if _is_ws_closed_error(e):
            return
        log.warning("clearAudio send failed: %s", e)


async def send_audio(ws: WebSocket, wav_or_pcm: bytes, frame_ms: int = 100, pace_realtime: bool = True) -> None:
    """Send audio bytes back to Vobiz as playAudio events.

    Vobiz disconnects if we burst large audio faster than realtime.
    Larger frames (100ms) work better than 20ms on Windows because Windows's
    asyncio.sleep is imprecise below ~15ms, causing jitter and audio breakage.
    We pace slightly faster than realtime (frame_ms-10ms) so Vobiz's playback
    buffer stays full and audio sounds continuous.

    If the WS is closed mid-send (caller hung up), exit the loop quietly
    instead of raising — that keeps a hangup from looking like a backend bug
    in the logs/ASGI error handler.
    """
    pcm, sr, _ch = strip_wav_header(wav_or_pcm)
    # Send slightly faster than realtime so buffer stays full.
    frame_sleep = max((frame_ms - 10) / 1000.0, 0.005)
    for frame in chunk_pcm(pcm, frame_ms=frame_ms, sample_rate=sr):
        if not frame:
            continue
        try:
            await ws.send_text(
                json.dumps(
                    {
                        "event": "playAudio",
                        "media": {
                            "contentType": "audio/x-l16",
                            "sampleRate": sr,
                            "payload": pcm_to_b64(frame),
                        },
                    }
                )
            )
        except BaseException as e:
            if _is_ws_closed_error(e):
                return
            raise
        if pace_realtime:
            await asyncio.sleep(frame_sleep)


def _sync_session_slots(session: "CallSession", current_user_text: str = "") -> None:
    """Sync caller_name + focused_program + programs_discussed from the
    authoritative source (session.history) before each new turn runs.

    Idempotent — safe to call multiple times. Reads everything from history
    (which engine.py updates even when a turn is cancelled mid-stream), so
    it can't lose state to cancellation races the way the old in-run_turn
    update path did.

    Args:
        session: the active CallSession.
        current_user_text: the user message about to be sent into the LLM,
            included in the program-pivot check so an explicit pivot
            ("હવે મારે MBA વિશે જાણવું છે") flips focus *before* the turn
            runs, not after.
    """
    from app.brain.catalog import get_catalog

    catalog = get_catalog()

    # 1) Continuous name extraction. The greet asks for the name, but callers
    #    sometimes skip it and dive into a question; in those cases we still
    #    pick up a name they drop on turn 3 or 5 ("મારું નામ સત્યેન છે"). We
    #    stop trying once we have one.
    if session.caller_name is None:
        for t in session.history.turns:
            if t.role != "user":
                continue
            name = _extract_caller_name(t.text or "")
            if name:
                session.caller_name = name
                log.info("call %s caller_name resolved: %r", session.call_id, name)
                break

    # 2) Pivot detection — if the user JUST mentioned a different program by
    #    name, switch focus immediately. This wins over any older history
    #    mention because the user's most recent intent dominates.
    #
    # PRONOUN GUARD (2026-05-21). When the user's text starts with a
    # demonstrative pronoun ('આ' / 'તે' / 'એ' = this/that) followed by a
    # GENERIC family word ('ડિપ્લોમા' / 'કોર્સ' / 'પ્રોગ્રામ' / 'કોલેજ'),
    # the user is referring to the program they're ALREADY discussing —
    # not naming a new variant. Don't pivot; keep the current focused
    # program. Without this guard, "આ ડિપ્લોમાના કાઉન્સિલરનું નામ" (the
    # diploma's counsellor — meaning the diploma they were just on, Civil)
    # got matched to 'Diploma (Automobile)' (first diploma in catalog),
    # poisoning the rest of the call.
    if current_user_text and not _looks_pronoun_reference(current_user_text):
        mentioned_by_user = catalog.find_by_name(current_user_text)
        if mentioned_by_user is not None:
            new_prog = (mentioned_by_user.get("program") or "").strip()
            current = ((session.focused_program or {}).get("program") or "").strip()
            if new_prog and new_prog != current:
                log.info(
                    "call %s focused_program: %r -> %r (user mentioned)",
                    session.call_id, current or None, new_prog,
                )
                session.focused_program = mentioned_by_user
            if new_prog and new_prog not in session.programs_discussed:
                session.programs_discussed.append(new_prog)
    elif current_user_text:
        log.info(
            "call %s pronoun reference %r — keeping focused_program %r",
            session.call_id, current_user_text[:60],
            (session.focused_program or {}).get("program") if session.focused_program else None,
        )

    # 3) If no focus is set yet, fall back to scanning the most recent
    #    assistant turns for a program name. This catches the case where
    #    the assistant introduced a program ("B.Tech Marine માટે…") and the
    #    user followed up with "હમ" — focus must persist across that filler.
    if session.focused_program is None:
        for t in reversed(session.history.turns):
            if t.role != "assistant":
                continue
            mentioned_by_asst = catalog.find_by_name(t.text or "")
            if mentioned_by_asst is not None:
                new_prog = (mentioned_by_asst.get("program") or "").strip()
                log.info(
                    "call %s focused_program: None -> %r (assistant mentioned in history)",
                    session.call_id, new_prog,
                )
                session.focused_program = mentioned_by_asst
                if new_prog and new_prog not in session.programs_discussed:
                    session.programs_discussed.append(new_prog)
                break


def _extract_caller_name(text: str) -> str | None:
    """Pull a name out of the caller's turn-1 reply.

    Patterns we handle (real transcripts from the log):
      - "મારું નામ X છે"            ("my name is X")
      - "My name is X"
      - "I'm X" / "I am X"
      - "X બોલું છું" / "X બોલું છે"
      - just the name (single token after "fine" or by itself)

    Returns the cleaned name or None if nothing plausible was found. The
    regex deliberately stops at sentence-final particles ("છે", "છું", ".")
    so we don't capture filler.
    """
    import re as _re

    if not text:
        return None
    cleaned = text.strip().rstrip(".!?,").strip()
    patterns = [
        r"મારું\s*નામ\s*(.+?)(?:\s*છે|$)",
        r"નામ\s*(.+?)(?:\s*છે|$)",
        r"\bmy\s*name\s*is\s*(.+)$",
        r"\bi\s*am\s*(.+)$",
        r"\bi'?m\s*(.+)$",
        # Python \b doesn't fire reliably on Gujarati combining marks after
        # 'બોલું' — use an explicit follow-set instead (space / end / punct).
        r"^(.+?)\s*બોલું(?:\s|$|[.,?!])",
    ]
    for pat in patterns:
        m = _re.search(pat, cleaned, flags=_re.IGNORECASE)
        if m:
            name = m.group(1).strip()
            # Drop leading filler ("hello", "haa", "ji") that sometimes
            # leaks into the capture.
            name = _re.sub(r"^(hello|hi|hey|haa+|haa|હા|હું|जी|ji)\b\s*", "", name, flags=_re.IGNORECASE).strip()
            # Drop leading Gujarati copula ("છે", "છું", etc.) — happens when
            # caller said "મારું નામ છે જયેશ" and the regex captures
            # "છે જયેશ" instead of "જયેશ". 2026-05-25 UI display bug.
            name = _re.sub(r"^(છે|છું|છો|છીએ|है|हूँ|is|am|are)\s+", "", name, flags=_re.IGNORECASE).strip()
            # Drop trailing copula too ("જયેશ છે")
            name = _re.sub(r"\s+(છે|છું|છો|છીએ|है|हूँ|is|am|are)$", "", name, flags=_re.IGNORECASE).strip()
            if 2 <= len(name) <= 40:
                # Reject if the captured "name" contains non-name junk tokens
                # (numbers, common Gujarati phrases) — see _looks_like_name below.
                if _looks_like_real_name(name):
                    return name
    # Bare-name fallback (2026-05-21 demo fix): the greet ENDS with
    # "તમારું શુભ નામ જણાવશો?" so the caller's turn-1 reply is often
    # just their name — "રોનક", "Ronak", "Ronak Ronak", etc. — no
    # "મારું નામ" prefix. Accept if: ≤3 words, ≥3 chars per word,
    # mostly letters, and not in the non-name token list. Without this,
    # the LLM saw a bare name as "unclear input" and replied "didn't
    # hear, please repeat" — frustrating on the first turn.
    bare = cleaned.strip()
    if bare:
        # Strip per-word punctuation so "Ronak, Ronak" dedupes correctly.
        raw_words = bare.split()
        words = [w.strip(",.;:!?") for w in raw_words if w.strip(",.;:!?")]
        # Drop common Gujarati copula/verb particles that leak into bare-
        # name replies: "છે જયેશ" / "જયેશ છે" / "છું રોનક" → just the
        # name. Without this, the DB shows "છે જયેશ" as the caller name.
        _COPULA_WORDS = {
            "છે", "છું", "છો", "છીએ", "છે.", "છું.",
            "हे", "हूँ", "है",
            "is", "am", "are",
        }
        words = [w for w in words if w.lower() not in _COPULA_WORDS and w not in _COPULA_WORDS]
        if 1 <= len(words) <= 3:
            # Dedupe repeated tokens
            if len(set(w.lower() for w in words)) == 1:
                bare_one = words[0]
            else:
                bare_one = " ".join(words)
            if _looks_like_real_name(bare_one):
                return bare_one
    return None


_NON_NAME_TOKENS: tuple[str, ...] = (
    # Common Gujarati words that aren't names but might appear in short replies
    "નંબર", "આપજો", "આપો", "કહો", "બોલો", "વારુ", "સારું", "બરાબર",
    "fine", "okay", "ok", "ઠીક", "good", "free", "ફ્રી",
    "hello", "હલો", "હલ્લો", "જી", "ji",
    "yes", "no", "હા", "ના",
    "નામ", "name",  # they'd say "નામ ___" not just "નામ"
    "પ્રોગ્રામ", "course", "કોર્સ",
    "ફી", "fee", "fees",
    # Conversational fillers / copulas / pronouns mis-captured as names in
    # production (2026-05-26): 'ચાલો' (let's go), 'છે'/'છું' (is/am),
    # 'કેમ' (how), 'શું' (what), pronouns. Reject so the lead name isn't
    # poisoned with junk like 'ચાલો' or 'છે'.
    "ચાલો", "ચાલ", "છે", "છું", "છો", "છીએ",
    "કેમ", "શું", "હું", "મને", "તમે", "તમારું", "મારું",
    "કેમ છો", "બોલું", "વાત",
)


def _looks_like_real_name(s: str) -> bool:
    """Reject obvious non-names (digits, common phrases). Used by
    _extract_caller_name to avoid resolving e.g. 'નંબર વન' as a name.

    Token matching uses word boundaries so 'yes' doesn't false-match
    inside 'Jayesh' (j-a-yes-h substring collision — production bug
    2026-05-21). For ASCII tokens we use \\b; for Gujarati tokens we
    require space/end-of-string on either side (Python \\b is
    unreliable on Gujarati script).
    """
    if not s:
        return False
    # Has digits? Not a name.
    if any(c.isdigit() for c in s):
        return False
    padded = " " + s.lower() + " "
    raw_padded = " " + s + " "
    for tok in _NON_NAME_TOKENS:
        tok_low = tok.lower()
        # ASCII tokens: word-boundary match. Pad with spaces both sides
        # so 'yes' matches ' yes ' but NOT ' jayesh '.
        if tok_low.isascii():
            if " " + tok_low + " " in padded:
                return False
        else:
            # Gujarati / Devanagari tokens — explicit space-or-edge match
            if " " + tok + " " in raw_padded:
                return False
    return True


_ELIGIBILITY_NOUNS = (
    "પાસ કર્યું", "પાસ થયો", "પાસ થઈ", "પાસ છે",
    "10th", "12th", "+2", "+ 2", "દસમું", "બારમું",
    "ધોરણ", "marks", "ગુણ", "percentage", "પર્સન્ટ",
    "પાત્ર", "લાયક", "eligible", "eligibility",
    "lateral", "diploma થી",
)
_ELIGIBILITY_VERBS = (
    "લઈ શકું", "કરી શકું", "જઈ શકું", "મળશે",
    "can i", "શું હું", "could i", "may i",
    "જોઈએ", "જરૂરી", "needed",
)


# Pronoun + generic-stub markers. If the user text matches this shape, we
# treat the utterance as a back-reference to the focused program rather
# than a fresh program mention. Prevents the "આ ડિપ્લોમા" → wrong-Diploma
# pivot from the 2026-05-21 call.
_PRONOUN_REF_RE = re.compile(
    r"^\s*(?:આ|તે|એ|એના|એનો|એની|તેના|તેનો|તેની|this|that|its)\s+"
    r"(?:ડિપ્લોમા|કોર્સ|પ્રોગ્રામ|કોલેજ|યુનિવર્સિટી|બીટેક|એમટેક|બીએસસી|એમએસસી|એમબીએ|બીબીએ|બીસીએ|"
    r"diploma|course|program|programme|college|university|btech|mtech|bsc|msc|mba|bba|bca|mca|degree)"
    r"(?=\s|$|[\.,\?ના|નો|ની|માં|ની])",  # Gujarati \b doesn't fire on script chars; use explicit follow-set
    re.IGNORECASE,
)


# Meta-question markers (Round 7, 2026-05-21). Caller asks ABOUT the
# conversation itself — what did they ask, what's their name, what did the
# agent say. These must NOT go through RAG (which would dump random
# programs into context and tempt the LLM to hallucinate). They must go
# through the conversation history, which the LLM already has in messages.
_META_Q_PATTERNS: tuple[str, ...] = (
    # Gujarati — "what did I ask / say / what was my name". Each pattern
    # is prefixed-checked with a leading space (added at match time) so
    # 'મારું' doesn't substring-collide with 'તમારું' (= 'your', a
    # different sense). 2026-05-21 false-positive: "તમારું નામ શું છે?"
    # (asking AGENT's name) was wrongly classified as meta until this
    # boundary check.
    "મેં શું પૂછ્યું", "મેં શું કહ્યું", "મેં ક્યું પૂછ્યું",
    "મેં કયો", "મેં કયા", "મેં કયું", "મેં શેમાં", "મેં શેના",
    "પૂછ્યું મેં", "પૂછ્યું હતું", "પૂછેલું",
    "મારું નામ", "મારું શુભ નામ",
    "પહેલો ક્વેશ્ચન", "પહેલો સવાલ", "પ્રથમ પ્રશ્ન", "પહેલા શું", "આગળ શું પૂછ્યું",
    "તમે શું કહ્યું", "તમે હમણાં શું", "તમે અગાઉ શું",
    "આ કોલમાં મેં", "આ કૉલમાં મેં",
    "મેં ક્યારે", "ક્યારે ઇન્ટ્રેસ્ટ",
    "મેં અત્યાર સુધી", "મેં અગાઉ",
    # Hindi
    "मैंने क्या पूछा", "मेरा नाम क्या",
    # English
    "what did i ask", "what was my question", "what was my name",
    "what's my name", "what is my name", "what did you say",
    "what did i say", "first question", "previous question",
    "what was that", "earlier",
)


# Name-STATEMENT patterns. When the user is GIVING their name ("મારું નામ
# રોનાક છે" / "my name is Ronak"), the text contains 'મારું નામ' which
# would otherwise false-fire the meta-question detector. Anything that
# matches these is treated as a normal statement, NOT a meta-question.
# Production bug 2026-05-21: caller said "મારું નામ રોનાક છે" → engine
# went into META_QUESTION_MODE, skipped RAG, gave a confused reply.
_NAME_STATEMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Negative lookahead excludes meta-keywords (યાદ / ભૂલી / શું / etc.)
    # so 'મારું નામ યાદ છે?' (meta) still fires while 'મારું નામ રોનક છે'
    # (statement) is correctly identified.
    re.compile(
        r"(?:મારુ|મારું)\s*નામ\s+"
        r"(?!યાદ|ભૂલી|શું|ક્યું|કયું|બોલો|બોલશો)"
        r"\S{2,}\s*(?:છે|હું|બોલ)",
        re.IGNORECASE,
    ),
    re.compile(r"\bmy\s+name\s+is\s+\w+", re.IGNORECASE),
    re.compile(r"\bi['']?m\s+\w+", re.IGNORECASE),
    re.compile(r"\bi\s+am\s+\w+", re.IGNORECASE),
    re.compile(r"\S+\s+બોલું", re.IGNORECASE),
)


def _looks_like_meta_question(text: str) -> bool:
    """Return True if the user is asking ABOUT the conversation itself
    (what did I ask, what's my name, etc.). These need a different
    routing — skip RAG, answer from history. Without this the agent
    runs RAG on the meta-question text, gets random program hits, and
    hallucinates 'you asked about BCA' (2026-05-21 prod bug).

    Matching is anchored on word boundaries: the pattern must follow a
    space (or be at the very start of the text). Otherwise 'મારું'
    substring-collides inside 'તમારું' (= 'your'), which has the
    opposite sense.

    EARLY EXIT (2026-05-21 fix): a name STATEMENT ('મારું નામ X છે' /
    'my name is X') contains 'મારું નામ' but it's the user GIVING their
    name, not asking what it is — return False so the engine doesn't
    flip into META mode and skip RAG.
    """
    if not text:
        return False
    # Don't fire on name-statements
    for pat in _NAME_STATEMENT_PATTERNS:
        if pat.search(text):
            return False
    padded = " " + text.lower() + " "
    raw_padded = " " + text + " "
    for p in _META_Q_PATTERNS:
        # ASCII patterns: match in lowercased padded text
        needle_l = " " + p.lower()
        if needle_l in padded:
            return True
        # Gujarati / Devanagari patterns: lower() is a no-op; check raw with leading space
        needle_r = " " + p
        if needle_r in raw_padded:
            return True
    return False


def _looks_pronoun_reference(text: str) -> bool:
    """True if the user is referring to the ALREADY focused program via
    a demonstrative pronoun + generic stub ('આ ડિપ્લોમા' / 'તે કોર્સ' /
    'this program'). Used by _sync_session_slots to skip find_by_name
    so the focused program stays put."""
    if not text:
        return False
    return bool(_PRONOUN_REF_RE.match(text.strip()))


def _looks_like_eligibility_question(text: str) -> bool:
    """Return True if `text` looks like 'am I qualified for X?'.

    Used by the bridge to suppress stream/family override when the caller
    is asking ELIGIBILITY (which should land in RAG → program's eligibility
    field), not browsing a stream category. Fixes the 2026-05-21 bug where
    '10th પાસ થયો, B.Tech લઈ શકું?' triggered the Diploma stream listing
    and the agent answered 'no info'.

    Requires BOTH an eligibility noun (10th/marks/percentage/eligible) AND
    a capability verb (can I / લઈ શકું / મળશે). One alone is too loose
    — 'મારે 12th પાસ વાળી નોકરી' is not an eligibility question.
    """
    if not text:
        return False
    low = text.lower()
    has_noun = any(n.lower() in low for n in _ELIGIBILITY_NOUNS) or any(n in text for n in _ELIGIBILITY_NOUNS)
    has_verb = any(v.lower() in low for v in _ELIGIBILITY_VERBS) or any(v in text for v in _ELIGIBILITY_VERBS)
    return has_noun and has_verb


def _b64_decode(s: str) -> bytes:
    import base64

    try:
        return base64.b64decode(s)
    except Exception:
        return b""


def _p50(xs: list[int]) -> int | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[len(s) // 2]
