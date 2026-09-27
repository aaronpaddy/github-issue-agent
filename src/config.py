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
    keep_workspaces: bool = False  # keep each run's clone and venv (debugging)

    # Webhook service
    webhook_secret: str | None = None
    trigger_label: str = "agent"
    redis_url: str = "redis://localhost:6379/0"
    allowed_repos: str = ""  # comma-separated owner/name list; empty means only github_repo
    job_timeout_seconds: int = 1800
    service_host: str = "127.0.0.1"
    service_port: int = 8000

    @property
    def allowed_repo_list(self) -> list[str]:
        listed = [r.strip() for r in self.allowed_repos.split(",") if r.strip()]
        return listed or [self.github_repo]


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # populated from env/.env
