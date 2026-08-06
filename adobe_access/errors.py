from __future__ import annotations

"""Turn raw exception text into a title + a short list of likely causes.

Kept framework-agnostic (no Streamlit import) so it stays unit-testable; the
UI wraps this with a small renderer in app.py.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FriendlyError:
    title: str
    reasons: list[str] = field(default_factory=list)
    retryable: bool = True


_CONNECTION_REASONS = [
    "VPN is disconnected",
    "Adobe is temporarily unavailable",
    "A corporate proxy or firewall is blocking the request",
]
_TIMEOUT_REASONS = [
    "Adobe is slow to respond right now",
    "Network latency on VPN or proxy",
    "The request was larger than usual",
]
_AUTH_REASONS = [
    "The technical account's credentials expired or were rotated",
    "The organization ID does not match the configured technical account",
    "The requested action is outside the account's granted scopes",
]
_CONFIG_REASONS = [
    "ADOBE_ORG_ID, ADOBE_CLIENT_ID, ADOBE_CLIENT_SECRET, or ADOBE_SCOPES is missing from .env",
    "The app was not restarted after .env was last edited",
]


def friendly_error(exc: BaseException) -> FriendlyError:
    message = str(exc).strip()
    lowered = message.lower()

    if "credentials are incomplete" in lowered or "adobe is not configured" in lowered:
        return FriendlyError("Adobe is not configured", _CONFIG_REASONS, retryable=False)
    if "writes are disabled" in lowered:
        return FriendlyError("Live writes are disabled", [message], retryable=False)
    if "cannot connect" in lowered:
        return FriendlyError("Adobe connection failed", _CONNECTION_REASONS)
    if "timed out" in lowered or "timeout" in lowered:
        return FriendlyError("Adobe request timed out", _TIMEOUT_REASONS)
    if "http 401" in lowered or "http 403" in lowered:
        return FriendlyError("Adobe rejected the request (permission denied)", _AUTH_REASONS, retryable=False)
    if "http 429" in lowered:
        return FriendlyError("Adobe is rate-limiting requests", ["Too many requests were sent in a short period. Wait a moment and retry."])
    if "http 5" in lowered and "adobe returned http 5" in lowered:
        return FriendlyError("Adobe returned a server error", ["Adobe is having a temporary issue on their end."])
    if "enter a complete email" in lowered or "invalid email" in lowered:
        return FriendlyError("That email address looks incomplete or invalid", [message] if message else [], retryable=False)
    if "not found" in lowered:
        return FriendlyError("Not found", [message] if message else [], retryable=False)

    return FriendlyError("Something went wrong", [message] if message else ["An unexpected error occurred."])
