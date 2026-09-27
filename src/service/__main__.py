"""Run the webhook receiver: `python -m src.service` (127.0.0.1:8000 unless configured)."""

from __future__ import annotations

import uvicorn

from src.config import load_settings
from src.service.app import create_app


def main() -> None:
    settings = load_settings()
    uvicorn.run(create_app(settings), host=settings.service_host, port=settings.service_port)


if __name__ == "__main__":
    main()
