from __future__ import annotations

"""Retry policy for transient Adobe failures during Execute.

Classification is delegated to `errors.friendly_error(exc).retryable`, so the
"what's transient vs. permanent" decision lives in exactly one place:
timeouts, connection failures, and HTTP 429/5xx are retried with exponential
backoff; invalid email, permission denied (401/403), and missing
configuration are not.
"""

import time
from dataclasses import dataclass
from typing import Callable, TypeVar

from .errors import friendly_error

T = TypeVar("T")

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 1.0
DEFAULT_MAX_DELAY_SECONDS = 8.0


@dataclass
class RetryResult:
    value: object = None
    success: bool = False
    attempts: int = 0
    retries: int = 0
    last_error: str = ""


def backoff_delay(attempt: int, base: float = DEFAULT_BASE_DELAY_SECONDS, cap: float = DEFAULT_MAX_DELAY_SECONDS) -> float:
    """Exponential backoff for the Nth attempt (1-indexed), capped at `cap`."""
    return min(base * (2 ** (attempt - 1)), cap)


def call_with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY_SECONDS,
    max_delay: float = DEFAULT_MAX_DELAY_SECONDS,
    sleep: Callable[[float], None] | None = None,
) -> RetryResult:
    """Call fn(), retrying transient failures with exponential backoff.

    Stops immediately, without retrying, when `errors.friendly_error(exc).retryable`
    is False (invalid email, permission denied, missing configuration, ...).

    `sleep` defaults to `time.sleep`, resolved at call time (not bind time) so
    tests can monkeypatch `adobe_access.retry.time.sleep` without needing to
    thread a fake through every caller.
    """
    sleep_fn = sleep or time.sleep
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        try:
            value = fn()
            return RetryResult(value=value, success=True, attempts=attempt, retries=attempt - 1, last_error="")
        except Exception as exc:
            last_error = str(exc)
            if not friendly_error(exc).retryable or attempt == max_attempts:
                return RetryResult(value=None, success=False, attempts=attempt, retries=attempt - 1, last_error=last_error)
            sleep_fn(backoff_delay(attempt, base_delay, max_delay))
    return RetryResult(value=None, success=False, attempts=max_attempts, retries=max_attempts - 1, last_error=last_error)
