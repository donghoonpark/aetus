from __future__ import annotations

import os
from dataclasses import dataclass


def _parse_origin_list(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(slots=True)
class Settings:
    postgres_dsn: str = "postgresql://aetus:aetus@127.0.0.1:15432/aetus"
    redis_url: str | None = "redis://127.0.0.1:16379/0"
    cache_ttl_seconds: int = 30
    feature_ttl_seconds: int = 7 * 24 * 60 * 60
    compression_minimum_size: int = 1024
    max_raw_drilldown_seconds: int = 60
    max_points_default: int = 1500
    cors_origins: tuple[str, ...] = ("http://127.0.0.1:4173", "http://localhost:4173")
    host: str = "0.0.0.0"
    port: int = 8000

    @classmethod
    def from_env(cls) -> "Settings":
        redis_url = os.getenv("AETUS_QUERY_REDIS_URL", "redis://127.0.0.1:16379/0")
        if redis_url.strip().lower() in {"", "none", "disabled"}:
            redis_url = None
        return cls(
            postgres_dsn=os.getenv("AETUS_POSTGRES_DSN", "postgresql://aetus:aetus@127.0.0.1:15432/aetus"),
            redis_url=redis_url,
            cache_ttl_seconds=int(os.getenv("AETUS_QUERY_CACHE_TTL_SECONDS", "30")),
            feature_ttl_seconds=int(os.getenv("AETUS_QUERY_FEATURE_TTL_SECONDS", str(7 * 24 * 60 * 60))),
            compression_minimum_size=int(os.getenv("AETUS_QUERY_COMPRESSION_MIN_SIZE", "1024")),
            max_raw_drilldown_seconds=int(os.getenv("AETUS_QUERY_MAX_RAW_DRILLDOWN_SECONDS", "60")),
            max_points_default=int(os.getenv("AETUS_QUERY_MAX_POINTS_DEFAULT", "1500")),
            cors_origins=_parse_origin_list(
                os.getenv("AETUS_CORS_ORIGINS", "http://127.0.0.1:4173,http://localhost:4173")
            ),
            host=os.getenv("AETUS_HOST", "0.0.0.0"),
            port=int(os.getenv("AETUS_PORT", "8000")),
        )
