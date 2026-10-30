"""Streaming LLM wrapper around OpenAI chat completions."""
from __future__ import annotations

from collections.abc import AsyncIterator

from openai import AsyncOpenAI


class LLM:
    def __init__(
        self,
        openai_client: AsyncOpenAI,
        model: str,
        system_prompt: str,
        temperature: float = 0.3,
        # 120 → 240 (2026-05-27): Gujarati is VERY token-heavy in the OpenAI
        # tokenizer (each glyph ≈ 2-3 tokens), so 120 tokens was only ~60-80
        # characters — info-rich replies (scholarship details, program lists,
        # eligibility + a follow-up question) hit the cap and got cut off
        # MID-SENTENCE on live calls. A caller literally flagged it ("token
        # flushing issue"). 240 gives a complete 2-3 sentence Gujarati reply
        # room to finish. NOTE: this does NOT raise latency — output is
        # streamed, so time-to-first-audio is unchanged; the model still
        # obeys the system prompt's "1-2 short sentences" brevity rule, it
        # just isn't truncated when a legitimately complete reply runs a
        # little past 120 tokens.
        max_tokens: int = 240,
    ) -> None:
        self.client = openai_client
        self.model = model
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.max_tokens = max_tokens

    def _build_messages(
        self,
        history: list[dict],
        context: str | None,
        language_hint: str | None,
    ) -> list[dict]:
        sys = self.system_prompt
        if language_hint:
            sys += f"\nReply in {language_hint}."
        if context:
            sys += f"\n\nReference context:\n{context}"
        return [{"role": "system", "content": sys}, *history]

    async def stream(
        self,
        history: list[dict],
        context: str | None = None,
        language_hint: str | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        """Stream tokens. `temperature` overrides the default for this call —
        used by engine.py to drop temp to 0.0 on fact-heavy turns (any turn
        with a program record in context), keeping the model rigidly
        grounded so it can't 'creatively' round fee numbers."""
        messages = self._build_messages(history, context, language_hint)
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=self.max_tokens,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    async def complete(
        self,
        history: list[dict],
        context: str | None = None,
        language_hint: str | None = None,
    ) -> str:
        parts: list[str] = []
        async for tok in self.stream(history, context, language_hint):
            parts.append(tok)
        return "".join(parts)
