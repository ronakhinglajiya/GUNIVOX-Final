"""Per-call in-memory turn history.

Persistence to DB happens at end of call (Phase 4). For now this is
the source of truth during an active call.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Role = Literal["user", "assistant", "system"]


@dataclass
class Turn:
    role: Role
    text: str
    language: str | None = None
    latency_ms: int | None = None


@dataclass
class TurnHistory:
    call_id: str
    turns: list[Turn] = field(default_factory=list)
    detected_language: str = "gu"

    def add_user(self, text: str, language: str | None = None) -> None:
        self.turns.append(Turn(role="user", text=text, language=language))
        if language:
            self.detected_language = language

    def add_assistant(self, text: str, latency_ms: int | None = None) -> None:
        self.turns.append(Turn(role="assistant", text=text, latency_ms=latency_ms))

    def to_openai_messages(self, max_turns: int = 8) -> list[dict]:
        """Last 8 turns = ~4 user/AI exchanges. Smaller LLM input = faster
        first-token. The model still has context for stickiness within a
        topic; older banter doesn't help and just adds tokens to score."""
        recent = self.turns[-max_turns:]
        return [{"role": t.role, "content": t.text} for t in recent]

    def __len__(self) -> int:
        return len(self.turns)
