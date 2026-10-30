"""Simple amplitude-based VAD audio segmenter.

Consumes raw PCM L16 mono frames, yields complete utterances. An utterance
ends when we see `silence_ms` of low-amplitude audio after at least
`min_speech_ms` of audible audio.

Far less smart than a real VAD library, but it works for phone-quality audio
where the signal/noise ratio is good enough that amplitude tracks speech.
"""
from __future__ import annotations

import struct
from collections import deque
from dataclasses import dataclass


def _max_abs(pcm: bytes) -> int:
    if not pcm:
        return 0
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm[: (len(pcm) // 2) * 2])
    return max(abs(s) for s in samples) if samples else 0


@dataclass
class VADConfig:
    sample_rate: int = 16000
    # Amplitude threshold (linear16) above which a frame counts as speech.
    # Phone audio noise floor is ~50-200; speech is >500.
    speech_threshold: int = 400
    # Need this much speech before we'll consider an utterance has started.
    min_speech_ms: int = 200
    # End the utterance after this much continuous silence post-speech.
    # Bumped 500→1000ms on 2026-05-21: callers were getting cut off mid-
    # sentence ("ઓકે ઓકે અને મને એક બીજી વસ્તુ જણાવો કે અગર હું") because
    # the natural breath-pause between clauses crossed 500ms. 1000ms still
    # keeps turn-end latency snappy but tolerates a natural conversational
    # pause inside a single utterance.
    silence_ms: int = 1000
    # Drop utterances shorter than this (probably noise).
    min_utterance_ms: int = 350
    # Cap an utterance at this length even if the user keeps talking.
    # Lowered 20000→8000ms on 2026-05-25 demo-eve: on speakerphone/noisy-room
    # callers the per-frame silence counter never reaches `silence_ms` because
    # background noise keeps spiking above `speech_threshold`. Result: the
    # utterance stayed open forever, no STT call ever fired, and the call
    # ended with turns=0. 8s is comfortably long for any single phone
    # utterance (typical = 2–4s) and short enough that we ALWAYS ship
    # something to STT well before a caller would give up and hang up.
    max_utterance_ms: int = 8000


class AudioSegmenter:
    """Feed `add_frame(pcm)` repeatedly; call `pop_utterance()` to retrieve
    a complete utterance (or None if nothing's ready)."""

    def __init__(self, cfg: VADConfig | None = None) -> None:
        self.cfg = cfg or VADConfig()
        self._buf = bytearray()
        self._utterance: bytearray | None = None
        self._silence_ms_run = 0
        self._speech_ms_total = 0
        self._utterance_ms = 0
        self._ready: deque[bytes] = deque()

    def add_frame(self, pcm: bytes) -> None:
        if not pcm:
            return
        bytes_per_ms = self.cfg.sample_rate * 2 / 1000
        frame_ms = int(len(pcm) / bytes_per_ms)
        is_speech = _max_abs(pcm) >= self.cfg.speech_threshold

        if self._utterance is None:
            # Not yet in an utterance — start when we see enough speech.
            if is_speech:
                self._speech_ms_total += frame_ms
                self._buf.extend(pcm)
                if self._speech_ms_total >= self.cfg.min_speech_ms:
                    # Promote buffer to active utterance
                    self._utterance = self._buf
                    self._buf = bytearray()
                    self._utterance_ms = self._speech_ms_total
                    self._silence_ms_run = 0
            else:
                # Drop pre-utterance silence (don't waste memory)
                self._speech_ms_total = 0
                self._buf.clear()
            return

        # We're in an utterance — keep appending.
        self._utterance.extend(pcm)
        self._utterance_ms += frame_ms

        if is_speech:
            self._silence_ms_run = 0
        else:
            self._silence_ms_run += frame_ms

        # End the utterance on enough trailing silence OR max length
        if (
            self._silence_ms_run >= self.cfg.silence_ms
            or self._utterance_ms >= self.cfg.max_utterance_ms
        ):
            if self._utterance_ms >= self.cfg.min_utterance_ms:
                self._ready.append(bytes(self._utterance))
            # Reset
            self._utterance = None
            self._utterance_ms = 0
            self._speech_ms_total = 0
            self._silence_ms_run = 0
            self._buf.clear()

    def pop_utterance(self) -> bytes | None:
        return self._ready.popleft() if self._ready else None

    def has_utterance(self) -> bool:
        return bool(self._ready)
