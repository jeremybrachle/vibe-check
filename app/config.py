from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
