"""Per-turn dialog action recommender.

Layer 4 of the human-brain roadmap (2026-05-21).

State (Layer 2) and Signals (Layer 3) tell us WHERE the conversation
is and HOW the caller feels. Dialog (Layer 4) tells us what the
counsellor SHOULD DO next — proactively, not reactively. The shift is
from "answer questions" to "move the conversation forward".

Two actions, deterministic computation (<1ms, no LLM):

  steer_to_close
    Trigger: ≥4 user turns, fee already quoted, no callback offered yet,
             not ready_to_enroll yet, not in price_shock / confused.
    Meaning: caller has enough info, no commitment signal — agent should
             offer "shall I connect you with the counsellor?" or
             "shall I WhatsApp you admission details?". 1 sentence,
             no pressure.

  ask_qualifier
    Trigger: caller's current text mentions a BACKGROUND only (PCM, PCB,
             Commerce, 10th, 12th, Science background) without naming
             a specific program.
    Meaning: agent should ask ONE qualifying question to narrow down,
             not dump 8 program names.

Both render to a terse hint string that flows into the same combined
SESSION SLOTS message used by state + signals. The system prompt has
matching behavioural directives so the LLM knows what to do when
either flag fires.
"""
from __future__ import annotations

from dataclasses import dataclass

# Background-only markers — student tells you their academic background
# but doesn't pick a program. These should trigger a qualifying question,
# NOT a 10-program list. (System prompt already has matching
# "BACKGROUND-AWARE SUGGESTIONS" rules for PCM/PCB/Commerce; this flag
# makes the trigger deterministic instead of relying on LLM judgement.)
_BACKGROUND_TOKENS: tuple[str, ...] = (
    # English
    "pcm", "pcb", "commerce", "science background", "arts background",
    "10th pass", "12th pass", "tenth pass", "twelfth pass",
    "10+2", "10 + 2", "12+2", "after 12th", "after 10th",
    # Gujarati
    "પીસીએમ", "પીસીબી", "કોમર્સ", "સાયન્સ બેકગ્રાઉન્ડ",
    "દસમું", "બારમું", "ધોરણ 10", "ધોરણ 12",
    "10th પાસ", "12th પાસ", "10મું પાસ", "12મું પાસ",
    # Hindi
    "दसवीं पास", "बारहवीं पास",
)


@dataclass
class TurnDialog:
    steer_to_close: bool = False
    ask_qualifier: bool = False
    matched: dict[str, str] | None = None


def compute_dialog(
    history,
    state,
    signals,
    current_user_text: str,
) -> TurnDialog:
    """Return the dialog-action flags for this turn.

    Args:
        history: TurnHistory (read-only).
        state: CallState — needed for turn_count + told/asked + commitments.
        signals: TurnSignals — to gate close-offer (don't push during
            price_shock / confused).
        current_user_text: the user's just-spoken transcript.
    """
    dialog = TurnDialog()
    matched: dict[str, str] = {}

    # ── steer_to_close ──
    # Conditions are conjunctive — all must hold.
    # Round 14: bumped down from ≥4 to ≥3 turns. Lead-gen reach calls
    # benefit from earlier momentum: caller has the fee + duration by
    # turn 2-3 and is in the highest-intent window to offer a close.
    if (
        state.user_turn_count >= 3
        and state.quoted_fee
        and not state.promised_callback
        and not signals.ready_to_enroll
        and not signals.confused
        and not signals.price_shock
    ):
        dialog.steer_to_close = True
        matched["steer_to_close"] = (
            f"turns={state.user_turn_count},fee_quoted,no_commitment"
        )

    # ── ask_qualifier ──
    if current_user_text:
        low = current_user_text.lower()
        hit = None
        for tok in _BACKGROUND_TOKENS:
            tlow = tok.lower()
            if tlow in low or tok in current_user_text:
                hit = tok
                break
        if hit:
            # Confirm the caller hasn't ALSO named a specific program in
            # the same utterance — if they have, the bridge's normal
            # focused_program path takes over (we don't want to ask
            # "which program?" when they already said B.Tech Marine).
            from app.brain.catalog import get_catalog
            if get_catalog().find_by_name(current_user_text) is None:
                dialog.ask_qualifier = True
                matched["ask_qualifier"] = hit

    dialog.matched = matched or None
    return dialog


def render_dialog_for_llm(dialog: TurnDialog) -> str:
    """Render as a terse single-line hint for the LLM.

    Empty string when nothing fires so the engine can skip injection.
    Matches the state-brief / signals-hint format: flag list + an
    action directive.
    """
    flags: list[str] = []
    if dialog.steer_to_close:
        flags.append("steer_to_close")
    if dialog.ask_qualifier:
        flags.append("ask_qualifier")
    if not flags:
        return ""
    return ",".join(flags)
