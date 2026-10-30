"""Build Plivo-style AnswerXML for Vobiz.

Vobiz fetches the answer_url when the call is answered and expects XML.
We return a <Stream> directive that opens a bidirectional WebSocket to our bridge.
"""
from __future__ import annotations

from xml.sax.saxutils import escape


def stream_response(
    ws_url: str,
    track: str = "inbound",
    bidirectional: bool = True,
    keepCallAlive: bool = True,
    sample_rate: int = 16000,
    intro_text: str | None = None,
    intro_voice: str = "WOMAN",
    intro_language: str = "en-IN",
) -> str:
    """Return Vobiz AnswerXML that opens a bidirectional <Stream> to our WS.

    Vobiz audioTrack values: 'inbound', 'outbound', 'both'.
    When bidirectional=true, audioTrack MUST be 'inbound' (Vobiz rejects others).
    Supported contentType:
      audio/x-l16;rate=8000  |  audio/x-l16;rate=16000  |  audio/x-mulaw;rate=8000

    intro_text: optional <Speak> played before <Stream> opens (diagnostic only).
    """
    if track not in {"inbound", "outbound", "both"}:
        track = "inbound"
    if bidirectional:
        track = "inbound"  # Vobiz constraint

    bidi = "true" if bidirectional else "false"
    keep = "true" if keepCallAlive else "false"

    intro = ""
    if intro_text:
        intro = (
            f'<Speak voice="{intro_voice}" language="{intro_language}">'
            f"{escape(intro_text)}"
            "</Speak>"
        )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        + intro
        + (
            f'<Stream bidirectional="{bidi}" audioTrack="{track}" '
            f'contentType="audio/x-l16;rate={sample_rate}" '
            f'keepCallAlive="{keep}">'
            f"{escape(ws_url)}"
            "</Stream>"
        )
        + "</Response>"
    )


def conference_response(
    room: str,
    enter_sound: str | None = None,
    wait_sound: str | None = None,
    record: bool = False,
    end_on_exit: bool = False,
) -> str:
    attrs = []
    if enter_sound:
        attrs.append(f'enterSound="{escape(enter_sound)}"')
    if wait_sound:
        attrs.append(f'waitSound="{escape(wait_sound)}"')
    if record:
        attrs.append('record="true"')
    if end_on_exit:
        attrs.append('endConferenceOnExit="true"')
    attr_str = (" " + " ".join(attrs)) if attrs else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Conference{attr_str}>{escape(room)}</Conference>"
        "</Response>"
    )


def hangup_response() -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>'


def play_then_hangup_response(audio_url: str) -> str:
    """Return AnswerXML that <Play>s an audio file then hangs up.

    Used for the inbound greeting flow: someone calls our public number,
    Vobiz fetches this XML, plays the WAV (a single self-contained message
    that reads out the admissions number), then drops the line. No
    bidirectional streaming, no WebSocket, no LLM — just one canned message.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Play>{escape(audio_url)}</Play>"
        "<Hangup/>"
        "</Response>"
    )


def speak_response(text: str, voice: str = "WOMAN", language: str = "en-IN") -> str:
    """Static fallback only — we'd normally use our own TTS via <Stream>."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Speak voice="{voice}" language="{language}">{escape(text)}</Speak>'
        "</Response>"
    )
