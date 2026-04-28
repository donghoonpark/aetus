from __future__ import annotations

import uvicorn

from aetus_ingest.app import create_app
from aetus_ingest.config import Settings


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(create_app(settings=settings), host=settings.host, port=settings.port)
