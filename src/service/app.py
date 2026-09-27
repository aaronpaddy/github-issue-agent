"""The FastAPI webhook receiver.

It does the minimum synchronously (verify, decide, enqueue) and answers quickly, because
GitHub gives webhooks only a few seconds. The slow work happens in the worker.
"""

from __future__ import annotations

import json

import structlog
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from rq import Queue

from src.config import Settings
from src.service.queue import enqueue_issue, get_queue
from src.service.webhook import parse_trigger, verify_signature

logger = structlog.get_logger(__name__)


def create_app(settings: Settings, queue: Queue | None = None) -> FastAPI:
    if not settings.webhook_secret:
        raise RuntimeError("WEBHOOK_SECRET must be set: refusing to accept unsigned webhooks.")
    secret = settings.webhook_secret
    allowed_repos = settings.allowed_repo_list
    job_queue = queue or get_queue(settings)

    app = FastAPI(title="github-issue-agent")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/github")
    async def github_webhook(
        request: Request,
        x_github_event: str = Header(default=""),
        x_hub_signature_256: str | None = Header(default=None),
    ) -> JSONResponse:
        body = await request.body()
        if not verify_signature(secret, body, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="invalid signature")

        if x_github_event == "ping":
            return JSONResponse({"status": "pong"})

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="body is not valid JSON") from None

        trigger = parse_trigger(
            x_github_event,
            payload,
            trigger_label=settings.trigger_label,
            allowed_repos=allowed_repos,
        )
        if trigger is None:
            return JSONResponse({"status": "ignored"})

        if not enqueue_issue(job_queue, trigger, settings.job_timeout_seconds):
            logger.info("duplicate trigger", repo=trigger.repo, issue=trigger.issue_number)
            return JSONResponse({"status": "duplicate"})

        logger.info("queued", repo=trigger.repo, issue=trigger.issue_number, by=trigger.sender)
        return JSONResponse({"status": "queued"}, status_code=202)

    return app
