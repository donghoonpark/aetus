from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class RateLimitPlan:
    rate_per_second: float
    burst: int


@dataclass
class _BucketState:
    tokens: float
    last_refill: float


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, _BucketState] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, plan: RateLimitPlan) -> bool:
        now = time.monotonic()
        with self._lock:
            state = self._buckets.get(key)
            if state is None:
                self._buckets[key] = _BucketState(tokens=plan.burst - 1, last_refill=now)
                return True

            elapsed = now - state.last_refill
            replenished = elapsed * plan.rate_per_second
            state.tokens = min(plan.burst, state.tokens + replenished)
            state.last_refill = now
            if state.tokens < 1:
                return False

            state.tokens -= 1
            return True
