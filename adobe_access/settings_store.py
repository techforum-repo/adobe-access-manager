from __future__ import annotations

"""Effective settings: SQLite overrides layered on top of the .env-backed defaults.

Only non-secret, operational fields live here. Adobe credentials and the
ADOBE_WRITE_ENABLED flag are never editable from the UI and always come from
`.env` via `adobe_access.config.settings` — see the Settings page for why.
"""

from dataclasses import dataclass
from typing import Any, Callable

from . import database
from .config import settings

_BOOL_TRUE = {"1", "true", "yes", "on"}


def _to_bool(value: str) -> bool:
    return str(value).strip().lower() in _BOOL_TRUE


def _from_bool(value: bool) -> str:
    return "true" if value else "false"


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str  # "str" | "int" | "bool"
    default: Callable[[], Any]
    help: str = ""


FIELDS: list[Field] = [
    Field(
        "allowed_email_domains", "Allowed email domains", "str",
        lambda: settings.allowed_email_domains,
        "Comma-separated list, e.g. example.com",
    ),
    Field(
        "default_country", "Default country", "str",
        lambda: settings.default_country,
        "Two-letter country used when a new Adobe user would be created.",
    ),
    Field(
        "default_identity_type", "Default identity type", "str",
        lambda: settings.default_identity_type,
        "federatedID, enterpriseID, or adobeID.",
    ),
    Field(
        "cache_ttl_seconds", "Cache TTL (seconds)", "int",
        lambda: settings.cache_ttl_seconds,
        "How long a group sync is considered fresh before it's flagged stale on the dashboard.",
    ),
    Field(
        "auto_adobe_validation", "Auto Adobe validation", "bool",
        lambda: True,
        "Automatically look up users in Adobe as soon as the Validate step opens.",
    ),
]

_FIELDS_BY_KEY = {field.key: field for field in FIELDS}


def _parse(field: Field, raw: str) -> Any:
    if field.kind == "int":
        return int(raw)
    if field.kind == "bool":
        return _to_bool(raw)
    return raw


# These are global app settings (one shared SQLite row set, not per-session
# state), so a process-wide cache is safe as long as every writer below
# invalidates it — `save()` and `reset()` are the only two. Read-heavy
# call sites (e.g. AdobeUMAPIClient.provision(), called once per user during
# a bulk Execute) would otherwise pay a full DB round-trip per user just to
# re-read values that can't have changed mid-request.
_cache: dict[str, Any] | None = None


def _invalidate_cache() -> None:
    global _cache
    _cache = None


def current_values() -> dict[str, Any]:
    """Effective values: a saved DB override if present, else the .env-backed default."""
    global _cache
    if _cache is not None:
        return _cache
    overrides = database.get_setting_overrides()
    values: dict[str, Any] = {}
    for field in FIELDS:
        raw = overrides.get(field.key)
        if raw is None:
            values[field.key] = field.default()
            continue
        try:
            values[field.key] = _parse(field, raw)
        except (TypeError, ValueError):
            values[field.key] = field.default()
    _cache = values
    return values


def overridden_keys() -> set[str]:
    return set(database.get_setting_overrides())


def save(values: dict[str, Any], actor: str) -> None:
    serialized: dict[str, str] = {}
    for key, value in values.items():
        field = _FIELDS_BY_KEY.get(key)
        if field is None:
            continue
        serialized[key] = _from_bool(bool(value)) if field.kind == "bool" else str(value)
    database.set_setting_overrides(serialized, actor)
    _invalidate_cache()


def reset() -> None:
    database.clear_setting_overrides([field.key for field in FIELDS])
    _invalidate_cache()


# --- Convenience accessors used elsewhere in the app --------------------------------

def allowed_domains() -> set[str]:
    raw = str(current_values()["allowed_email_domains"])
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def default_country() -> str:
    return str(current_values()["default_country"])


def default_identity_type() -> str:
    return str(current_values()["default_identity_type"])


def cache_ttl_seconds() -> int:
    try:
        return int(current_values()["cache_ttl_seconds"])
    except (TypeError, ValueError):
        return settings.cache_ttl_seconds


def auto_adobe_validation() -> bool:
    return bool(current_values()["auto_adobe_validation"])
