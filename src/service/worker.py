"""Worker entrypoint: `python -m src.service.worker`.

Uses SimpleWorker, which runs each job in the worker's own process rather than forking. Forking
is fragile on macOS, and a job here is one long-running run anyway.
"""

from __future__ import annotations

from redis import Redis
from rq import Queue, SimpleWorker

from src.config import load_settings
from src.service.queue import QUEUE_NAME


def main() -> None:
    settings = load_settings()
    connection = Redis.from_url(settings.redis_url)
    SimpleWorker([Queue(QUEUE_NAME, connection=connection)], connection=connection).work()


if __name__ == "__main__":
    main()
