"""Audio helpers for the Vobiz bridge.

Sarvam Bulbul v2 TTS returns a WAV (with 44-byte RIFF header) + PCM L16 payload.
Plivo/Vobiz <Stream> playAudio expects raw PCM L16 base64, no header.
"""
from __future__ import annotations

import base64
import io
import struct
import wave


def strip_wav_header(wav_bytes: bytes) -> tuple[bytes, int, int]:
    """Return (pcm_payload, sample_rate, channels). Falls back to slice-from-44 if parsing fails."""
    if len(wav_bytes) < 44 or not wav_bytes.startswith(b"RIFF"):
        return wav_bytes, 16000, 1
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            channels = w.getnchannels()
            sample_rate = w.getframerate()
            n_frames = w.getnframes()
            pcm = w.readframes(n_frames)
        return pcm, sample_rate, channels
    except Exception:
        return wav_bytes[44:], 16000, 1


def pcm_to_b64(pcm: bytes) -> str:
    return base64.b64encode(pcm).decode("ascii")


def b64_to_pcm(b64: str) -> bytes:
    return base64.b64decode(b64)


def chunk_pcm(pcm: bytes, frame_ms: int = 20, sample_rate: int = 16000) -> list[bytes]:
    """Split a PCM buffer into N-millisecond frames (L16 mono = 2 bytes/sample)."""
    bytes_per_frame = (sample_rate * frame_ms // 1000) * 2
    return [pcm[i : i + bytes_per_frame] for i in range(0, len(pcm), bytes_per_frame)]


def pcm_to_wav(pcm: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """Re-wrap raw PCM L16 in a minimal WAV container (for recording to disk)."""
    bits_per_sample = 16
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    subchunk2_size = len(pcm)
    chunk_size = 36 + subchunk2_size
    header = b"RIFF" + struct.pack("<I", chunk_size) + b"WAVE"
    header += b"fmt " + struct.pack(
        "<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits_per_sample
    )
    header += b"data" + struct.pack("<I", subchunk2_size)
    return header + pcm
