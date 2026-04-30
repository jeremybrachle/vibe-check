from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


_REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    app_name: str = "The Vibe Check"
    app_env: str = "development"
    database_url: str = "sqlite:///./vibe_check.db"

    refresh_every_hours: int = 2

    hn_max_per_feed: int = 30
    hn_max_total: int = 100
    hn_include_top_comments: bool = True
    hn_top_comments_per_story: int = 3

    reddit_enabled: bool = True
    reddit_subreddits: str = "programming,machinelearning,LocalLLaMA,opensource,webdev,compsci"
    reddit_max_per_subreddit: int = 25
    reddit_max_total: int = 120
    reddit_user_agent: str = "vibe-check-bot/0.1 (no-auth public feed fetch)"

    # LLM provider: none | auto | heuristic | openai | ollama
    llm_provider: str = "none"

    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o-mini"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "openchat"

    # CORS: comma-separated list of allowed origins, or * for dev
    allowed_origins: str = "*"

    # Optional token to protect /admin/refresh — leave empty to disable the check
    admin_token: SecretStr = SecretStr("")

    # --- Research feeds (/api/v1/feeds/*) ------------------------------------
    feeds_enable_arxiv: bool = True
    feeds_enable_reddit: bool = True
    feeds_enable_hn: bool = True
    feeds_arxiv_min_delay_s: float = 3.1
    feeds_request_timeout_s: float = 8.0
    feeds_default_lookback_hours: int = 24
    feeds_max_items_per_response: int = 200
    feeds_user_agent: str = "vibe-check-feeds/0.1 (research; contact via repo)"
    feeds_topics_file: Path = _REPO_ROOT / "app" / "services" / "feeds" / "topics.yaml"
    feeds_cache_dir: Path = _REPO_ROOT / "data" / "feeds_cache"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
