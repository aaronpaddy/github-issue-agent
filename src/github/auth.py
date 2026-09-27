"""Decides who the agent acts as on GitHub.

With a GitHub App configured, the agent authenticates as the app's
installation on the target repo, so its pull requests, comments, and commits
show up as `<app-name>[bot]` rather than as the developer. Without one it
falls back to a personal access token and a generic commit identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from github.GithubException import GithubException, UnknownObjectException

from github import Auth, Github, GithubIntegration
from src.config import Settings

FALLBACK_COMMIT_NAME = "Issue Agent"
FALLBACK_COMMIT_EMAIL = "agent@issue-agent.local"


class GitHubAuthError(Exception):
    pass


@dataclass
class GitHubAuth:
    token: str
    commit_name: str
    commit_email: str
    identity: str  # for logs only


def resolve_auth(settings: Settings, repo_full_name: str) -> GitHubAuth:
    if settings.github_app_id and settings.github_app_private_key_path:
        return _app_auth(settings, repo_full_name)
    if settings.github_token:
        return GitHubAuth(
            token=settings.github_token,
            commit_name=FALLBACK_COMMIT_NAME,
            commit_email=FALLBACK_COMMIT_EMAIL,
            identity="personal access token",
        )
    raise GitHubAuthError(
        "No GitHub credentials: set GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY_PATH, or GITHUB_TOKEN."
    )


def _app_auth(settings: Settings, repo_full_name: str) -> GitHubAuth:
    key_path = Path(settings.github_app_private_key_path or "").expanduser()
    if not key_path.is_file():
        raise GitHubAuthError(f"GitHub App private key not found at {key_path}")

    owner, name = repo_full_name.split("/", 1)
    integration = GithubIntegration(
        auth=Auth.AppAuth(settings.github_app_id or 0, key_path.read_text())
    )
    try:
        installation = integration.get_repo_installation(owner, name)
    except UnknownObjectException:
        raise GitHubAuthError(
            f"The GitHub App is not installed on {repo_full_name}. Install it on that "
            "repository from the app's settings page, then try again."
        ) from None
    except GithubException as e:
        raise GitHubAuthError(f"GitHub rejected the app credentials: {e.data}") from None

    token = integration.get_access_token(installation.id).token
    slug = installation.raw_data["app_slug"]
    bot_login = f"{slug}[bot]"
    bot_id = Github(auth=Auth.Token(token)).get_user(bot_login).id

    return GitHubAuth(
        token=token,
        commit_name=bot_login,
        commit_email=f"{bot_id}+{bot_login}@users.noreply.github.com",
        identity=f"GitHub App {bot_login}",
    )
