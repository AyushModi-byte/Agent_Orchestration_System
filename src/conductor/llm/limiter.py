"""Async token bucket per provider. Stays under free-tier RPM; backs off exponentially on 429."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


class RateLimiter:
    def __init__(
        self,
        rpm: dict[str, float],
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        base_backoff: float = 2.0,
        max_backoff: float = 60.0,
    ) -> None:
        self._interval = {p: 60.0 / r for p, r in rpm.items()}
        self._clock = clock
        self._sleep = sleep
        self._next_free: dict[str, float] = {}
        self._strikes: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._base = base_backoff
        self._max = max_backoff

    async def acquire(self, provider: str) -> None:
        """Reserve the next slot; sleeping happens outside the lock so slots stay ordered."""
        lock = self._locks.setdefault(provider, asyncio.Lock())
        async with lock:
            now = self._clock()
            slot = max(now, self._next_free.get(provider, now))
            self._next_free[provider] = slot + self._interval.get(provider, 0.0)
        if slot > now:
            await self._sleep(slot - now)

    def penalize(self, provider: str) -> float:
        """Push the provider's next slot out exponentially after a 429; returns the delay."""
        strikes = self._strikes.get(provider, 0) + 1
        self._strikes[provider] = strikes
        delay = min(self._base * float(2 ** (strikes - 1)), self._max)
        self._next_free[provider] = max(self._next_free.get(provider, 0.0), self._clock() + delay)
        return delay

    def reset(self, provider: str) -> None:
        self._strikes[provider] = 0
