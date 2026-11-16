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
    google_client_id: str = ""  # OAuth web client id; empty turns Google sign-in off

    # Providers, swappable by config. "a,b" means: try a, fall back to b.
    # cloud:<model> is any OpenAI-compatible API; Gemini by default. flash-lite answers in ~1-3 s, which a voice turn
    # needs; 3.5-flash-lite is the cloud fallback (3.8-flash timed out under load); ollama answers when the cloud is out.
    llm: str = "cloud:gemini-3.1-flash-lite,cloud:gemini-3.5-flash-lite,ollama:llama3.2"
    llm_api_key: str = ""  # Gemini key from aistudio.google.com/apikey (or Groq, OpenRouter, z.ai with their base URL)
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    stt: str = "whisper:small"
    tts: str = "say"
    ollama_url: str = "http://localhost:11434"

    max_upload_bytes: int = 5_000_000
    max_review_files: int = 40
    max_file_chars: int = 12_000
    max_text_chars: int = 4_000
    max_audio_s: int = 60
    agent_max_steps: int = 4

    max_workspace_bytes: int = 50_000_000
    public_url: str = "http://localhost:5173"  # where users reach the app; used for links and OAuth redirects

    # Sandbox for running user code. docker = isolated container (production);
    # seatbelt = macOS sandbox-exec profile (dev on a Mac without Docker); off = running code is disabled.
    sandbox: str = "docker"
    sandbox_image: str = "voxagent-sandbox:latest"
    # When the API itself runs in a container: preview servers join this Docker network and are reached by name.
    sandbox_network: str = ""
    sandbox_cpus: float = 1.0
    sandbox_memory_mb: int = 1024
    sandbox_timeout_s: int = 120
    sandbox_idle_s: int = 900
    sandbox_output_chars: int = 20_000
    fix_attempts: int = 3
    tunnel: str = ""  # "cloudflared" exposes previews on a public trycloudflare.com URL
    # Driving this Mac's screen (screen_control): off unless the owner turns it on; admin accounts only.
    screen_control: bool = False
    screen_steps: int = 12

    # Web access. search: duckduckgo (no key) or searxng:<base url>.
    web_search: str = "duckduckgo"
    web_fetch_bytes: int = 2_000_000

    # Integrations, each off until configured.
    google_client_secret: str = ""  # Gmail + Calendar connection (same OAuth client as sign-in)
    github_client_id: str = ""
    github_client_secret: str = ""
    deploy_provider: str = ""  # render:<deploy hook url> | vercel (needs the vercel CLI and VERCEL_TOKEN)
    telegram_token: str = ""
    whatsapp_token: str = ""
    whatsapp_phone_id: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""  # verifies X-Hub-Signature-256 on webhook calls
    vapid_private_key: str = ""  # web push; generate with: uv run python -m app.push keys
    vapid_subject: str = "mailto:admin@voxagent.local"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id: str = ""
    briefing_hour: int = 8  # local hour for the daily spoken briefing push

    free_daily_messages: int = 100
    free_daily_reviews: int = 10
    auth_rate_per_min: int = 10
    admin_emails: list[str] = []


@lru_cache
def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
