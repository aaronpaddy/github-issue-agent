"""Environment-driven settings. No secrets ever hardcoded or logged."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str
    github_token: str
    github_repo: str = "aaronpaddy/slack-mcp-server"
    claude_model: str = "claude-sonnet-5"
    max_attempts: int = 3


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # populated from env/.env
