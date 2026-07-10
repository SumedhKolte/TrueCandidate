"""Central configuration. pydantic-settings reads .env / environment."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str
    supabase_service_key: str

    groq_api_key: str
    groq_model: str = "llama-3.3-70b-versatile"

    # --- Real-meeting ingestion (Recall.ai meeting bot) ----------------------
    # Leave empty to run simulator-only; set all three to enable "paste a
    # meet link and the bot joins".
    recall_api_key: str = ""
    recall_api_base: str = "https://us-east-1.recall.ai/api/v1"
    # Public HTTPS base of THIS backend (Render URL or ngrok tunnel) — Recall
    # posts transcript/participant webhooks to {public_base_url}/webhook/recall
    public_base_url: str = ""

    # Self-hosted Playwright bot: a persistent Chrome profile that is already
    # signed into Google. Required because Meet blocks anonymous guests from
    # most meetings ("You can't join this video call").
    # Create it once with:  python -m bot.login
    meet_bot_profile_dir: str = ""
    # Hard per-call latency budget. The webhook pipeline must stay < 500ms
    # end-to-end, so the LLM gets ~450ms and everything else runs concurrently.
    groq_timeout_s: float = 0.45

    # Heuristic tunables — surfaced as config so ops can tune without deploys.
    passive_observer_after_s: int = 180      # 3 min silent + cam off => penalty
    greeting_response_window_s: int = 15     # who answers "Hi <name>" counts
    probe_after_ambiguous_s: int = 300       # 5 min stuck at 50/50 => probe
    sweep_interval_s: int = 2                # background heuristic cadence


@lru_cache
def get_settings() -> Settings:
    return Settings()
