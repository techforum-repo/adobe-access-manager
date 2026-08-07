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


# Adobe represents org-level administrative roles — System Administrator,
# Product Administrator (per product), Profile Administrator (per product
# profile, a finer-grained level than Product Administrator), Support
# Administrator, ... — as specially-named entries in a user's own `groups`
# list, distinct from the custom user groups this app provisions. They never
# pass client.is_user_group()'s filter on the groups-listing endpoint, so
# without this they'd silently vanish into membership_table()'s generic
# "ignored" count.
#
# Confirmed against real tenant data that the raw string Adobe returns for
# the same role type is NOT consistent — observed variants include an
# underscore-prefixed slug (`_product_admin`, `_developer`) and a
# human-readable phrase ("Product Administrator ..."). Matched by a
# case-insensitive prefix pattern on the "core" role phrase rather than an
# exact string, precisely because of that variability. The underscore-slug
# form only requires the short "admin" (the leading underscore is itself
# Adobe's reserved-name marker, a strong enough signal on its own); the
# non-underscore form requires the full word "Administrator" specifically —
# bare "Admin" without underscore is too generic and risks misfiring on a
# real custom group whose name happens to start with it (e.g. "Product Admin
# Access Group").
#
# Product/Profile Administrator apply per product or per product profile —
# whatever text remains after stripping the matched role phrase and its
# separators (underscore, hyphen, colon, parentheses) is kept as `detail`
# (e.g. the product name, "Target") so multiple entries of the same category
# can be grouped together instead of each showing as an opaque one-off.
@dataclass(frozen=True)
class SpecialPermission:
    category: str  # e.g. "Product Administrator", or the raw name if unrecognized
    detail: str  # e.g. "Target" — empty for org-wide roles with no per-item detail
    raw: str  # the original Adobe name, always preserved for verification
    recognized: bool  # matched a known role pattern, vs. falling back to the raw name


_SPECIAL_PERMISSION_EXACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^_?org[_ ]?admin$", re.I), "System Administrator"),
    (re.compile(r"^_?support[_ ]?admin$", re.I), "Support Administrator"),
    (re.compile(r"^_?deployment[_ ]?admin$", re.I), "Deployment Administrator"),
    (re.compile(r"^_?user[_ ]?group[_ ]?admin$", re.I), "User Group Administrator"),
    (re.compile(r"^_?licens\w*[_ ]?admin$", re.I), "License Administrator"),
    (re.compile(r"^_?storage[_ ]?admin$", re.I), "Storage Administrator"),
]
_SPECIAL_PERMISSION_PREFIX_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(_product[_ ]?admin(istrator)?|Product Administrator)", re.I), "Product Administrator"),
    (re.compile(r"^(_profile[_ ]?admin(istrator)?|Profile Administrator)", re.I), "Profile Administrator"),
    # "_developer..." always groups as Developer, whatever follows.
    (re.compile(r"^_developer", re.I), "Developer"),
    # Confirmed against real tenant data: an unqualified "_admin..." — no
    # "product"/"profile" immediately after the underscore, so this doesn't
    # overlap with the two patterns above — is this tenant's Profile
    # Administrator. Underscore-required, unlike the Product/Profile patterns
    # above — bare "Admin" without it is too generic to safely catch (see the
    # false-positive note in the module docstring), and no bare "Admin..."
    # variant has been reported here.
    (re.compile(r"^_admin", re.I), "Profile Administrator"),
]


def is_special_permission(name: str) -> bool:
    """True for an Adobe administrative-role entry, as opposed to an ordinary
    custom user group — either Adobe's own reserved-name convention (leading
    underscore) or a recognized role phrase (see module docstring above)."""
    raw = str(name).strip()
    if raw.startswith("_"):
        return True
    return classify_special_permission(raw).recognized


def classify_special_permission(name: str) -> SpecialPermission:
    """Best-effort classification. Falls back to the raw Adobe name as its own
    `category` (`recognized=False`) for anything unrecognized, rather than
    guessing — still visible, just not grouped under a friendly label."""
    raw = str(name).strip()
    for pattern, category in _SPECIAL_PERMISSION_EXACT_PATTERNS:
        if pattern.match(raw):
            return SpecialPermission(category=category, detail="", raw=raw, recognized=True)
    for pattern, category in _SPECIAL_PERMISSION_PREFIX_PATTERNS:
        match = pattern.match(raw)
        if match:
            remainder = raw[match.end():]
            detail = remainder.strip(" _-:()").replace("_", " ").strip()
            return SpecialPermission(category=category, detail=detail, raw=raw, recognized=True)
    return SpecialPermission(category=raw, detail="", raw=raw, recognized=False)


def describe_special_permission(name: str) -> str:
    """Single-line friendly label — classify_special_permission()'s category
    plus its detail in parentheses, if any."""
    result = classify_special_permission(name)
    return f"{result.category} ({result.detail})" if result.detail else result.category


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
