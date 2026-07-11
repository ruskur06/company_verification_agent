"""Lightweight abuse protection for public check requests.

The rate limiter is intentionally process-local for the MVP.
Production deployments with multiple workers should use a
shared store or reverse-proxy rate limiting.
"""

from __future__ import annotations

from collections import deque
from threading import Lock
from time import monotonic


PUBLIC_REQUEST_MAX_BODY_BYTES = 16_384
PUBLIC_REQUEST_RATE_LIMIT = 5
PUBLIC_REQUEST_RATE_WINDOW_SECONDS = 600


class InMemoryRateLimiter:
    """Thread-safe sliding-window request attempt tracker."""

    def __init__(
        self,
        *,
        max_attempts: int,
        window_seconds: int,
    ) -> None:
        if max_attempts < 1:
            raise ValueError(
                "max_attempts must be at least 1"
            )

        if window_seconds < 1:
            raise ValueError(
                "window_seconds must be at least 1"
            )

        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[
            str,
            deque[float],
        ] = {}
        self._lock = Lock()

    def allow(
        self,
        client_key: str,
        *,
        now: float | None = None,
    ) -> bool:
        """Record an allowed attempt or reject a full window."""
        current_time = (
            monotonic()
            if now is None
            else now
        )

        cutoff = (
            current_time
            - self.window_seconds
        )

        with self._lock:
            attempts = self._attempts.setdefault(
                client_key,
                deque(),
            )

            while (
                attempts
                and attempts[0] <= cutoff
            ):
                attempts.popleft()

            if len(attempts) >= self.max_attempts:
                return False

            attempts.append(current_time)
            return True

    def clear(self) -> None:
        """Clear tracked attempts, primarily for tests."""
        with self._lock:
            self._attempts.clear()


public_request_rate_limiter = InMemoryRateLimiter(
    max_attempts=PUBLIC_REQUEST_RATE_LIMIT,
    window_seconds=(
        PUBLIC_REQUEST_RATE_WINDOW_SECONDS
    ),
)
