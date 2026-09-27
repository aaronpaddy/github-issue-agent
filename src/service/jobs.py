"""What the worker runs for each queued issue."""

from __future__ import annotations

import structlog
from redis import Redis

from src.config import load_settings
from src.github.auth import resolve_auth
from src.github.client import GitHubClient
from src.github.threads import ERROR
from src.pipeline import run_issue
from src.service.queue import release_lock

logger = structlog.get_logger(__name__)


def run_issue_job(repo: str, issue_number: int, trigger: str = "label") -> str:
    settings = load_settings().model_copy(update={"github_repo": repo})
    try:
        result = run_issue(
            settings,
            issue_number,
            emit=lambda message: logger.info("run", message=message),
            trigger=trigger,
        )
        logger.info(
            "job finished", repo=repo, issue=issue_number, trigger=trigger, outcome=result.outcome.value
        )
        return result.outcome.value
    except Exception as e:
        logger.exception("job crashed", repo=repo, issue=issue_number)
        _report_crash(settings, issue_number, e)
        raise
    finally:
        release_lock(Redis.from_url(settings.redis_url), repo, issue_number)


def _report_crash(settings, issue_number: int, error: Exception) -> None:
    """Tell the issue something went wrong instead of leaving it in silence. Best effort."""
    try:
        auth = resolve_auth(settings, settings.github_repo)
        GitHubClient(token=auth.token, repo_full_name=settings.github_repo).comment_on_issue(
            issue_number,
            "The agent hit an unexpected error and made no changes "
            f"(`{type(error).__name__}`). The maintainers can check the worker logs.",
            ERROR,
        )
    except Exception:
        logger.exception("could not report the crash on the issue")
