from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VOX_", env_file=".env", extra="ignore")

    secret_key: str  # required; keys live only in the environment
    database_url: str = "sqlite:///./voxagent.db"
    data_dir: str = "./data"
    cors_origins: list[str] = ["http://localhost:5173"]
    cookie_secure: bool = False  # set true behind HTTPS

    access_ttl_s: int = 900
    refresh_ttl_s: int = 30 * 86400

    # Providers, swappable by config. "a,b" means: try a, fall back to b.
    llm: str = "ollama:llama3.2,ollama:qwen3-vl:8b-instruct"
    stt: str = "whisper:small"
    tts: str = "say"
    ollama_url: str = "http://localhost:11434"

    max_upload_bytes: int = 5_000_000
    max_review_files: int = 40
    max_file_chars: int = 12_000
    max_text_chars: int = 4_000
    max_audio_s: int = 60
    agent_max_steps: int = 4

    free_daily_messages: int = 100
    free_daily_reviews: int = 10
    auth_rate_per_min: int = 10
    admin_emails: list[str] = []


@lru_cache
def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
