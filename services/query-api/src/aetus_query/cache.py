from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class Cache(Protocol):
    def get_json(self, key: str) -> dict[str, Any] | None: ...

    def set_json(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None: ...


class NullCache:
    def get_json(self, key: str) -> dict[str, Any] | None:
        return None

    def set_json(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        del key, value, ttl_seconds


class RedisJsonCache:
    def __init__(self, url: str) -> None:
        self._client: Redis = Redis.from_url(url, decode_responses=True)

    def get_json(self, key: str) -> dict[str, Any] | None:
        try:
            raw = self._client.get(key)
        except RedisError as exc:
            logger.warning("redis get failed for key %s: %s", key, exc)
            return None
        if raw is None:
            return None
        return json.loads(raw)

    def set_json(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None:
        try:
            self._client.setex(key, ttl_seconds, json.dumps(value, separators=(",", ":"), ensure_ascii=True))
        except RedisError as exc:
            logger.warning("redis set failed for key %s: %s", key, exc)
