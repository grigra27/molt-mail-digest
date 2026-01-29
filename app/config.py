from dataclasses import dataclass
import os


def _get_env(name: str, default: str | None = None, required: bool = False) -> str:
    v = os.getenv(name, default)
    if required and (v is None or v == ""):
        raise RuntimeError(f"Missing required env var: {name}")
    return v  # type: ignore[return-value]


@dataclass(frozen=True)
class Config:
    imap_host: str
    imap_port: int
    imap_user: str
    imap_password: str
    imap_folder: str

    telegram_bot_token: str
    telegram_chat_id: int

    # LLM (Groq/OpenAI-compatible)
    llm_api_key: str
    llm_base_url: str
    llm_model: str

    tz: str
    schedule_hours: list[int]

    max_emails_per_run: int
    max_chars_per_email: int
    summary_max_output_tokens: int
    digest_max_output_tokens: int

    log_level: str


def load_config() -> Config:
    hours_raw = _get_env("SCHEDULE_HOURS", "10,12,14,16,18")
    schedule_hours = [int(x.strip()) for x in hours_raw.split(",") if x.strip()]

    # Backward-compatible fallbacks:
    # - If you still have OPENAI_API_KEY/OPENAI_MODEL in .env, it will work.
    llm_api_key = (
        os.getenv("LLM_API_KEY")
        or os.getenv("GROQ_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    )
    if not llm_api_key:
        raise RuntimeError("Missing required env var: LLM_API_KEY (or GROQ_API_KEY/OPENAI_API_KEY)")

    llm_base_url = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.groq.com/openai/v1"
    llm_model = os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL") or "llama-3.3-70b-versatile"

    return Config(
        imap_host=_get_env("IMAP_HOST", required=True),
        imap_port=int(_get_env("IMAP_PORT", "993")),
        imap_user=_get_env("IMAP_USER", required=True),
        imap_password=_get_env("IMAP_PASSWORD", required=True),
        imap_folder=_get_env("IMAP_FOLDER", "INBOX/ONLINE"),

        telegram_bot_token=_get_env("TELEGRAM_BOT_TOKEN", required=True),
        telegram_chat_id=int(_get_env("TELEGRAM_CHAT_ID", required=True)),

        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        llm_model=llm_model,

        tz=_get_env("TZ", "Europe/Moscow"),
        schedule_hours=schedule_hours,

        max_emails_per_run=int(_get_env("MAX_EMAILS_PER_RUN", "80")),
        max_chars_per_email=int(_get_env("MAX_CHARS_PER_EMAIL", "20000")),
        summary_max_output_tokens=int(_get_env("SUMMARY_MAX_OUTPUT_TOKENS", "220")),
        digest_max_output_tokens=int(_get_env("DIGEST_MAX_OUTPUT_TOKENS", "900")),

        log_level=_get_env("LOG_LEVEL", "INFO"),
    )
