"""Environment-driven settings. No secrets ever hardcoded or logged."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str | None = None  # falls back to the SDK's own credential lookup
    github_token: str | None = None  # personal access token; unused when a GitHub App is set
    github_app_id: int | None = None
    github_app_private_key_path: str | None = None
    github_repo: str = "aaronpaddy/slack-mcp-server"
    claude_model: str = "claude-sonnet-5"
    max_attempts: int = 2
    max_budget_usd: float = 1.00
    workspaces_dir: str = "workspaces"


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # populated from env/.env
