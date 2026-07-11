from __future__ import annotations

import pytest

from app.services.public_request_guard import (
    InMemoryRateLimiter,
)


def test_rate_limiter_allows_requests_below_limit():
    limiter = InMemoryRateLimiter(
        max_attempts=3,
        window_seconds=60,
    )

    assert limiter.allow(
        "client-a",
        now=100.0,
    )
    assert limiter.allow(
        "client-a",
        now=101.0,
    )
    assert limiter.allow(
        "client-a",
        now=102.0,
    )


def test_rate_limiter_rejects_request_above_limit():
    limiter = InMemoryRateLimiter(
        max_attempts=2,
        window_seconds=60,
    )

    assert limiter.allow(
        "client-a",
        now=100.0,
    )
    assert limiter.allow(
        "client-a",
        now=101.0,
    )
    assert not limiter.allow(
        "client-a",
        now=102.0,
    )


def test_rate_limiter_tracks_clients_separately():
    limiter = InMemoryRateLimiter(
        max_attempts=1,
        window_seconds=60,
    )

    assert limiter.allow(
        "client-a",
        now=100.0,
    )
    assert limiter.allow(
        "client-b",
        now=100.0,
    )

    assert not limiter.allow(
        "client-a",
        now=101.0,
    )
    assert not limiter.allow(
        "client-b",
        now=101.0,
    )


def test_rate_limiter_allows_request_after_window():
    limiter = InMemoryRateLimiter(
        max_attempts=1,
        window_seconds=60,
    )

    assert limiter.allow(
        "client-a",
        now=100.0,
    )

    assert not limiter.allow(
        "client-a",
        now=159.0,
    )

    assert limiter.allow(
        "client-a",
        now=160.0,
    )


def test_rate_limiter_clear_removes_attempts():
    limiter = InMemoryRateLimiter(
        max_attempts=1,
        window_seconds=60,
    )

    assert limiter.allow(
        "client-a",
        now=100.0,
    )

    assert not limiter.allow(
        "client-a",
        now=101.0,
    )

    limiter.clear()

    assert limiter.allow(
        "client-a",
        now=102.0,
    )


@pytest.mark.parametrize(
    ("max_attempts", "window_seconds"),
    [
        (0, 60),
        (-1, 60),
        (1, 0),
        (1, -1),
    ],
)
def test_rate_limiter_rejects_invalid_configuration(
    max_attempts,
    window_seconds,
):
    with pytest.raises(ValueError):
        InMemoryRateLimiter(
            max_attempts=max_attempts,
            window_seconds=window_seconds,
        )
