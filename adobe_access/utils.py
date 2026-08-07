from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

# Leading characters that make Excel/Sheets/Numbers interpret a CSV cell as a
# formula instead of literal text (CSV/formula injection, OWASP-recognized).
_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t", "\r")

# Required shape for a new user's email in the Provision wizard: exactly two
# parts separated by one dot, each letters optionally followed by trailing
# digits (e.g. "john.doe", "john2.doe" — a common disambiguation suffix when
# the plain name is already taken). Deliberately strict otherwise — no
# underscores, hyphens, leading/embedded digits, or extra parts — since this
# is specifically the org's account-naming convention, not general email syntax.
_FIRSTNAME_LASTNAME_RE = re.compile(r"[A-Za-z]+\d*\.[A-Za-z]+\d*")


@dataclass(frozen=True)
class ParsedName:
    first_name: str
    last_name: str
    ambiguous: bool


def normalize_email(value: str) -> str:
    return value.strip().lower()


def _strip_disambiguation_suffix(part: str) -> str:
    """Drop a trailing digit run — a disambiguation suffix like the "2" in
    "john2" for a "john" that was already taken, not part of the actual name.
    Falls back to the original part if stripping would empty it entirely
    (a part that's nothing but digits)."""
    return re.sub(r"\d+$", "", part) or part


def derive_name(email: str) -> ParsedName:
    local = email.split("@", 1)[0]
    parts = [p for p in re.split(r"[._-]+", local) if p]
    if not parts:
        return ParsedName("", "", True)
    first = _strip_disambiguation_suffix(parts[0]).title()
    last = " ".join(_strip_disambiguation_suffix(p).title() for p in parts[1:])
    ambiguous = len(parts) < 2
    return ParsedName(first, last, ambiguous)


def validate_email(email: str, allowed_domains: set[str]) -> tuple[bool, str]:
    email = normalize_email(email)
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        return False, "Invalid email format"
    local, domain = email.rsplit("@", 1)
    if allowed_domains and domain not in allowed_domains:
        return False, f"Only {', '.join(sorted(allowed_domains))} addresses are allowed"
    if not _FIRSTNAME_LASTNAME_RE.fullmatch(local):
        return False, "Email must be firstname.lastname@domain (letters, optionally a trailing digit, exactly one dot)"
    return True, ""


def classify_system(name: str) -> str:
    upper = name.upper()
    aliases = {
        "AEM": ("AEM", "EXPERIENCE MANAGER"),
        "AEP": ("AEP", "EXPERIENCE PLATFORM"),
        "CJA": ("CJA", "CUSTOMER JOURNEY ANALYTICS"),
        "Analytics": ("ANALYTICS",),
        "Target": ("TARGET",),
        "Campaign": ("CAMPAIGN",),
    }
    for system, tokens in aliases.items():
        if any(token in upper for token in tokens):
            return system
    return "Other"


def classify_environment(name: str) -> str:
    upper = name.upper()
    if any(x in upper for x in ("PROD", "PRODUCTION")):
        return "Production"
    if any(x in upper for x in ("DEV", "DEVELOPMENT")):
        return "Development"
    if any(x in upper for x in ("UAT", "STAGE", "QA", "TEST")):
        return "Non-production"
    return "Unspecified"


def is_privileged(name: str) -> bool:
    upper = name.upper()
    return any(x in upper for x in ("ADMIN", "OWNER", "DEVELOPER"))


def sanitize_csv_cell(value: Any) -> Any:
    """Neutralize CSV/formula injection: a cell starting with =, +, -, @, or a tab
    is prefixed with a single quote so spreadsheet apps treat it as literal text
    instead of executing it as a formula when a downstream user opens the export.
    Non-strings (numbers, booleans, None/NaN) pass through unchanged."""
    if not isinstance(value, str) or not value:
        return value
    return f"'{value}" if value[0] in _FORMULA_TRIGGER_CHARS else value


def safe_csv(df: pd.DataFrame, *, index: bool = False) -> str:
    """CSV export with formula-injection protection applied to every text column.

    Use this instead of calling `df.to_csv()` directly whenever a DataFrame may
    contain user-controlled free text (template names/descriptions, actor names,
    notes, audit details, ...) that a downstream admin might open in Excel/Sheets.
    """
    sanitized = df.copy()
    for column in sanitized.columns:
        if sanitized[column].dtype == object:
            sanitized[column] = sanitized[column].map(sanitize_csv_cell)
    return sanitized.to_csv(index=index)


def sanitize_log_field(value: Any) -> str:
    """Replace control characters (CR/LF/tab/...) with spaces before writing
    free-text user input (actor, details, ...) to the flat-file log — otherwise
    a crafted value (e.g. the free-text "Signed in as" field) could forge
    additional fake-looking log lines."""
    return "".join(" " if ord(ch) < 32 or ord(ch) == 127 else ch for ch in str(value))


def harden_file_permissions(path: Path, *, mode: int = 0o600) -> None:
    """Restrict a local data file (SQLite DB, log file) to the owning user only.

    Defaults to 0o600 (owner read/write). Pass mode=0o700 for a directory —
    it needs the execute bit to stay traversable, or later file operations
    inside it (e.g. RotatingFileHandler creating a rotated backup) will fail.

    POSIX only — chmod doesn't provide equivalent access control on Windows, so
    this is a no-op there; rely on filesystem/user-account isolation instead.
    Best-effort: never raises, so it can't block app startup or logging.
    """
    if os.name == "nt":
        return
    try:
        path.chmod(mode)
    except OSError:
        pass
