"""The job queue (Redis + RQ) and the lock that keeps one issue from running twice.

GitHub can redeliver a webhook, and people can toggle a label, so enqueueing takes a
per-issue lock first. The worker releases it when the run finishes; the TTL is a safety net
for a worker that dies mid-run.
"""

from __future__ import annotations

from redis import Redis
from rq import Queue

from src.config import Settings
from src.service.webhook import Trigger

QUEUE_NAME = "issue-agent"
JOB_FUNCTION = "src.service.jobs.run_issue_job"
LOCK_TTL_SECONDS = 3600


def get_queue(settings: Settings) -> Queue:
    return Queue(QUEUE_NAME, connection=Redis.from_url(settings.redis_url))


def lock_key(repo: str, issue_number: int) -> str:
    return f"issue-agent:lock:{repo.lower()}#{issue_number}"


def enqueue_issue(queue: Queue, trigger: Trigger, timeout_seconds: int) -> bool:
    """Queue a run. Returns False if that issue already has one queued or running."""
    acquired = queue.connection.set(
        lock_key(trigger.repo, trigger.issue_number), "1", nx=True, ex=LOCK_TTL_SECONDS
    )
    if not acquired:
        return False
    queue.enqueue(
        JOB_FUNCTION,
        trigger.repo,
        trigger.issue_number,
        trigger.kind,
        job_timeout=timeout_seconds,
        result_ttl=3600,
        failure_ttl=86400,
    )
    return True


def release_lock(connection: Redis, repo: str, issue_number: int) -> None:
    connection.delete(lock_key(repo, issue_number))
