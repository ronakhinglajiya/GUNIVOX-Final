from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="dev")
    log_level: str = Field(default="INFO")

    database_url: str = Field(
        default="postgresql+asyncpg://gunivox:gunivox@localhost:5432/gunivox"
    )
    database_url_sync: str = Field(
        default="postgresql+psycopg://gunivox:gunivox@localhost:5432/gunivox"
    )

    jwt_secret: str = Field(default="change-me-in-env")
    jwt_algorithm: str = Field(default="HS256")
    jwt_expire_minutes: int = Field(default=60 * 24)

    openai_api_key: str = Field(default="")
    openai_model: str = Field(default="gpt-4o-mini")

    sarvam_api_key: str = Field(default="")
    sarvam_base_url: str = Field(default="wss://api.sarvam.ai")
    sarvam_stt_url: str = Field(default="wss://api.sarvam.ai/speech-to-text/ws")
    sarvam_tts_url: str = Field(default="https://api.sarvam.ai/text-to-speech")
    sarvam_tts_model: str = Field(default="bulbul:v2")
    sarvam_tts_speaker: str = Field(default="anushka")  # try: manisha, vidya, priya, neha
    sarvam_stt_model: str = Field(default="saaras:v3")

    vobiz_auth_id: str = Field(default="")
    vobiz_auth_token: str = Field(default="")
    vobiz_base_url: str = Field(default="https://api.vobiz.ai")
    vobiz_caller_id: str = Field(default="")
    # Public URL that Vobiz can reach for AnswerXML + media stream.
    # E.g. https://gunivox.example.com when behind Cloudflare tunnel.
    vobiz_public_base_url: str = Field(default="")

    public_base_url: str = Field(default="http://localhost:8000")

    # PER-CAMPAIGN max concurrent outbound calls the dialer will originate.
    # 2026-06-02: dropped 5 → 1 per client requirement. A CSV upload of 100
    # leads now dials strictly one-at-a-time WITHIN A SINGLE CAMPAIGN —
    # lead 1 must FULLY end before lead 2 starts, etc. Avoids the "call
    # bomb" effect that was hammering Vobiz concurrency. Tick lowered
    # 30s → 5s so the next call kicks off within ~5s of the previous one
    # ending, instead of waiting up to 30s.
    # 2026-06-04: This is now the PER-CAMPAIGN cap. Multiple campaigns can
    # still run in parallel — each campaign respects its own cap of 1, so
    # two active campaigns yield two concurrent in-flight calls (one per
    # campaign). See dialer_max_concurrent_global for the system-wide cap.
    dialer_max_concurrent: int = Field(default=1)
    # SYSTEM-WIDE safety cap on total concurrent in-flight calls across ALL
    # campaigns combined. Prevents accidental overload if many campaigns
    # are active at once (e.g. 10 campaigns × per-campaign=1 would otherwise
    # originate 10 simultaneous calls). The dialer breaks out of its inner
    # loop as soon as EITHER the per-campaign cap or this global cap is hit.
    dialer_max_concurrent_global: int = Field(default=5)
    dialer_tick_seconds: float = Field(default=5.0)

    faiss_index_path: Path = Field(default=BACKEND_ROOT / "data" / "gunivox_brain.index")
    metadata_mapping_path: Path = Field(default=BACKEND_ROOT / "data" / "metadata_mapping.pkl")
    admission_brain_json: Path = Field(default=BACKEND_ROOT / "data" / "admission_brain.json")
    recordings_dir: Path = Field(default=BACKEND_ROOT / "recordings")

    system_prompt: str = Field(
        default=(
            "You are Gunivox, an admissions counsellor for Ganpat University. "
            "Speak warmly and concisely. Use the provided context to answer "
            "questions about programs, fees, eligibility, and counsellors. "
            "If the student wants to talk to a human, offer to transfer or "
            "schedule a callback."
        )
    )

    # 2026-06-23: hard campaign-wide BCA-only lock. When True, EVERY turn
    # gets a top-priority directive injected that restricts the pitch to the
    # 4 BCA programs and forces a polite pivot when the caller asks about
    # any other course. Flip to False via env (BCA_ONLY_CAMPAIGN=false) when
    # the arts-only campaign ends — no code change needed.
    # 2026-06-24: superseded by the B.Sc IT campaign below — default OFF now.
    bca_only_campaign: bool = Field(default=False)

    # 2026-06-24: hard campaign-wide B.Sc IT-only lock (DCS Main Campus only,
    # Ahmedabad city campus EXCLUDED). When True, EVERY turn gets a
    # top-priority directive injected that restricts the pitch to the 4
    # Main-Campus B.Sc IT programs and forces a polite pivot when the caller
    # asks about any other course. Mutually exclusive with the BCA lock above
    # (only one single-course campaign should be True at a time). Flip to
    # False via env (BSC_IT_ONLY_CAMPAIGN=false) when this campaign ends.
    bsc_it_only_campaign: bool = Field(default=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
