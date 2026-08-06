from __future__ import annotations

from adobe_access.errors import friendly_error


def test_connection_failure_is_classified_and_retryable():
    info = friendly_error(RuntimeError("Cannot connect to Adobe. Check VPN, proxy, and firewall. Endpoint: https://x"))
    assert info.title == "Adobe connection failed"
    assert info.reasons
    assert info.retryable is True


def test_missing_credentials_is_not_retryable():
    info = friendly_error(RuntimeError("Adobe credentials are incomplete. Update .env and restart the app."))
    assert info.title == "Adobe is not configured"
    assert info.retryable is False


def test_permission_denied_is_not_retryable():
    info = friendly_error(RuntimeError("Adobe returned HTTP 403: forbidden"))
    assert "permission denied" in info.title.lower()
    assert info.retryable is False


def test_rate_limit_is_retryable():
    info = friendly_error(RuntimeError("Adobe returned HTTP 429: too many requests"))
    assert "rate-limiting" in info.title.lower()
    assert info.retryable is True


def test_unknown_error_falls_back_to_generic_message():
    info = friendly_error(ValueError("something obscure happened"))
    assert info.title == "Something went wrong"
    assert info.reasons == ["something obscure happened"]
