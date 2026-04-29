from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class RateLimitPlan:
    rate_per_second: float
    burst: int


@dataclass
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: float = 0.0


@dataclass
class _BucketState:
    tokens: float
    last_refill: float


class InMemoryRateLimiter:
    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._buckets: dict[str, _BucketState] = {}
        self._lock = threading.Lock()
        self._clock = clock or time.monotonic

    def allow(self, key: str, plan: RateLimitPlan) -> bool:
        return self.consume(key, plan).allowed

    def consume(self, key: str, plan: RateLimitPlan) -> RateLimitDecision:
        if plan.rate_per_second <= 0:
            raise ValueError("rate_per_second must be greater than 0")
        if plan.burst <= 0:
            raise ValueError("burst must be greater than 0")

        now = self._clock()
        with self._lock:
            state = self._buckets.get(key)
            if state is None:
                self._buckets[key] = _BucketState(tokens=plan.burst - 1, last_refill=now)
                return RateLimitDecision(allowed=True)

            elapsed = now - state.last_refill
            replenished = elapsed * plan.rate_per_second
            state.tokens = min(plan.burst, state.tokens + replenished)
            state.last_refill = now
            if state.tokens < 1:
                retry_after = (1 - state.tokens) / plan.rate_per_second
                return RateLimitDecision(allowed=False, retry_after_seconds=retry_after)

            state.tokens -= 1
            return RateLimitDecision(allowed=True)
