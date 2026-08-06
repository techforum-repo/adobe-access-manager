from __future__ import annotations

from adobe_access.retry import call_with_retry


def test_succeeds_on_first_try_without_sleeping():
    sleeps = []
    result = call_with_retry(lambda: 42, sleep=sleeps.append)
    assert result.success is True
    assert result.value == 42
    assert result.attempts == 1
    assert result.retries == 0
    assert sleeps == []


def test_retries_transient_failure_then_succeeds():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("Adobe request timed out. Endpoint: https://x")
        return "ok"

    sleeps = []
    result = call_with_retry(flaky, sleep=sleeps.append)
    assert result.success is True
    assert result.value == "ok"
    assert result.attempts == 3
    assert result.retries == 2
    assert len(sleeps) == 2
    assert sleeps == sorted(sleeps)  # exponential: each delay >= the previous


def test_stops_immediately_on_permanent_failure():
    calls = {"n": 0}

    def always_permission_denied():
        calls["n"] += 1
        raise RuntimeError("Adobe returned HTTP 403: forbidden")

    sleeps = []
    result = call_with_retry(always_permission_denied, sleep=sleeps.append)
    assert result.success is False
    assert calls["n"] == 1  # no retry attempted
    assert result.retries == 0
    assert sleeps == []


def test_gives_up_after_max_attempts_on_persistent_transient_failure():
    calls = {"n": 0}

    def always_times_out():
        calls["n"] += 1
        raise RuntimeError("Adobe request timed out. Endpoint: https://x")

    sleeps = []
    result = call_with_retry(always_times_out, max_attempts=3, sleep=sleeps.append)
    assert result.success is False
    assert calls["n"] == 3
    assert result.retries == 2
    assert len(sleeps) == 2
