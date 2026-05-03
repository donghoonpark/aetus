from __future__ import annotations

import uvicorn

from aetus_query.config import Settings


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(
        "aetus_query.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
