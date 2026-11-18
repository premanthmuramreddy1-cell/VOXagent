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
    # cloud:<model> is any OpenAI-compatible API; Gemini by default. All free tiers, smartest first (ranked() keeps
    # this order): Groq gpt-oss-120b called tools right in 1.6 s; Gemini 3.8 Flash is smart but ~4.5 s; flash-lite
    # and the local ollama model are last resorts. (3.5-flash took ~20 s; Groq qwen3.8-27b sent broken tool calls.)
    llm: str = "groq:openai/gpt-oss-120b,cloud:gemini-3.8-flash,cloud:gemini-3.1-flash-lite,ollama:llama3.2"
    # Models for the skills that write and judge code (agent.CODE_SKILLS); empty means the same chain as `llm`.
    # scripts/model_bench.py (2026-11-18, 35 hidden tests): gemini-3.8-flash 35/35 ~20 s, gpt-oss-120b 30/35 ~8 s,
    # nemotron-super 30/35, kimi-k3.1-pro 20/35 (slow); code quality beats speed here, voice is not waiting on it.
    llm_code: str = ("cloud:gemini-3.8-flash,groq:openai/gpt-oss-120b,openrouter:nvidia/nemotron-3.5-super-vl:free,"
                     "cloud:gemini-3.1-flash-lite")
    llm_api_key: str = ""  # Gemini key from aistudio.google.com/apikey (or Groq, OpenRouter, z.ai with their base URL)
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    # groq:<model> in `llm`: models served by Groq (fast first tokens), a key from console.groq.com/keys
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # meta:<model> in `llm`: Meta's Llama API (OpenAI-compatible), a key from llama.developer.meta.com
    meta_api_key: str = ""
    meta_base_url: str = "https://api.llama.com/compat/v1"
    # openrouter:<model> in `llm`: any model OpenRouter serves, a key from openrouter.ai/keys
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # nvidia:<model> in `llm`: NVIDIA's hosted models (build.nvidia.com), a key from there
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    stt: str = "whisper:small"
    tts: str = "say"
    ollama_url: str = "http://localhost:11434"
    llm_first_token_s: float = 15  # a model silent this long hands the turn to the next one (voice needs fast replies)

    max_upload_bytes: int = 5_000_000
    max_review_files: int = 40
    max_file_chars: int = 12_000
    max_text_chars: int = 4_000
    max_audio_s: int = 60
    agent_max_steps: int = 8  # search, read, then act needs room; 4 cut multi-step tasks short

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
    # set when VoxAgent is reachable from the internet: only these emails may create an account from outside this Mac
    allowed_emails: list[str] = []


@lru_cache
def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
