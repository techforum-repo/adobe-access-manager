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
# Product Administrator, Profile Administrator, User Group Administrator,
# Support Administrator, ... — as specially-named entries in a user's own
# `groups` list, distinct from the custom user groups this app provisions.
# They never pass client.is_user_group()'s filter on the groups-listing
# endpoint, so without this they'd silently vanish into membership_table()'s
# generic "ignored" count.
#
# Per Adobe's own UMAPI documentation (adobe-apiplatform.github.io/umapi-documentation,
# "Managing Administrators"):
#   _org_admin, _support_admin, _deployment_admin  — fixed, org-wide roles
#   _product_admin_<ProductName>                    — Product Administrator
#   _admin_<ProductProfileName-or-UserGroupName>     — Profile OR User Group Administrator
#   _developer_<ProductProfileName>                  — Developer
# Adobe's docs are explicit that `_admin_<name>` is used for BOTH product
# profile admins and user group admins, with the identical prefix and no
# field anywhere distinguishing which — confirmed against this app's own
# real tenant data (two real examples, one of each role, differed only in
# which one happened to also be a synced custom group name; nothing in
# either raw string itself said which was which). Adobe's docs say resolving
# that requires cross-referencing `<name>` against a separate list of
# profiles/groups. The only such list this app has is its own synced
# custom-group cache — so `classify_special_permission()`'s
# `known_group_names` disambiguates by checking whether `<name>`
# (case-insensitively — group names aren't
# guaranteed consistent casing, same as everywhere else this app compares
# them) matches a real, currently-synced group. No match (or no cache
# supplied) falls back to Profile Administrator — this app never syncs
# product profiles separately to check against those instead.
#
# Adobe's docs also explicitly warn: "avoid any logic that expects fixed
# group names — these are liable to change without notice." Treat all of
# this as best-effort, not a guarantee: `raw` is always preserved so a wrong
# guess is still visible and reportable, never silently swallowed.
#
# Also confirmed against real tenant data that the raw string for the same
# role type is NOT always the underscore-prefixed slug Adobe's docs
# describe — a human-readable phrase ("Product Administrator ...") has been
# observed too. Matched by a case-insensitive prefix pattern rather than an
# exact string for that reason. The non-underscore form requires the full
# word "Administrator" specifically — bare "Admin" without underscore is too
# generic and risks misfiring on a real custom group whose name happens to
# start with it (e.g. "Product Admin Access Group").
#
# Product/Profile/User Group Administrator apply per product, product
# profile, or user group — whatever text remains after stripping the matched
# role phrase and its separators (underscore, hyphen, colon, parentheses) is
# kept as `detail` (e.g. the product name, "Target") so multiple entries of
# the same category can be grouped together instead of each showing as an
# opaque one-off.
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
]
_SPECIAL_PERMISSION_PREFIX_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(_product[_ ]?admin(istrator)?|Product Administrator)", re.I), "Product Administrator"),
    # Confirmed: "_developer_<ProductProfileName>", same shape as the
    # "_admin_<name>" ambiguity above but Adobe only uses it for one role, so
    # no disambiguation is needed here.
    (re.compile(r"^_developer", re.I), "Developer"),
    # The human-readable phrasing (as opposed to Adobe's documented
    # "_admin_<name>" slug — see _GENERIC_ADMIN_PATTERN below) names the role
    # explicitly, so it's unambiguous by construction and needs no
    # disambiguation against the group cache, unlike the slug form.
    (re.compile(r"^Profile Administrator", re.I), "Profile Administrator"),
    (re.compile(r"^User Group Administrator", re.I), "User Group Administrator"),
]
# See the module docstring above — genuinely ambiguous by Adobe's own design,
# resolved in classify_special_permission() via known_group_names, not here.
_GENERIC_ADMIN_PATTERN = re.compile(r"^_admin", re.I)


def is_special_permission(name: str) -> bool:
    """True for an Adobe administrative-role entry, as opposed to an ordinary
    custom user group — either Adobe's own reserved-name convention (leading
    underscore) or a recognized role phrase (see module docstring above)."""
    raw = str(name).strip()
    if raw.startswith("_"):
        return True
    return classify_special_permission(raw).recognized


_PRODUCT_PROFILE_ADMIN_CATEGORIES = {"Product Administrator", "Profile Administrator"}


def _drop_redundant_slug_suffix(detail: str) -> str:
    """Adobe product profile names tend to repeat the readable profile name
    immediately after itself as a lowercase, hyphenated slug, then tack on a
    technical environment/instance suffix — e.g. a profile literally named
    "Example Web Platform" showing up as "Example Web Platform-example-web-
    platform-prod-publish". A product name and a profile name are joined by
    " - " (spaces around the hyphen); the redundant slug repeat only ever
    shows up within the profile-name segment, glued on with a bare "-" (no
    spaces) — so only that last " - "-delimited segment is searched for a
    self-repeat, never the product name itself.
    """
    if " - " in detail:
        head, _, tail = detail.rpartition(" - ")
        return f"{head} - {_dedupe_slug_repeat(tail)}"
    return _dedupe_slug_repeat(detail)


def _dedupe_slug_repeat(segment: str) -> str:
    """Within one name segment, find where a leading portion's slugified form
    (lowercase, non-alphanumerics collapsed to hyphens) reappears verbatim
    right after it, and cut there. Returned as-is if no such repeat is found."""
    search_from = 0
    while True:
        hyphen_pos = segment.find("-", search_from)
        if hyphen_pos == -1:
            return segment
        candidate_name = segment[:hyphen_pos]
        candidate_slug = re.sub(r"[^a-z0-9]+", "-", candidate_name.lower()).strip("-")
        remainder = segment[hyphen_pos + 1:]
        if candidate_slug and (remainder == candidate_slug or remainder.startswith(candidate_slug + "-")):
            return candidate_name
        search_from = hyphen_pos + 1


def classify_special_permission(name: str, *, known_group_names: set[str] | None = None) -> SpecialPermission:
    """Best-effort classification. Falls back to the raw Adobe name as its own
    `category` (`recognized=False`) for anything unrecognized, rather than
    guessing — still visible, just not grouped under a friendly label.

    `known_group_names` — a case-insensitively-normalized (casefolded) set of
    currently-synced custom group names — disambiguates the generic
    "_admin_<name>" case (see the module docstring above): if `<name>`
    matches a real synced group, it's User Group Administrator for that
    group; otherwise Profile Administrator. Omit it (the default) to always
    get Profile Administrator for that case, e.g. when no group cache is
    available to check against.
    """
    raw = str(name).strip()
    for pattern, category in _SPECIAL_PERMISSION_EXACT_PATTERNS:
        if pattern.match(raw):
            return SpecialPermission(category=category, detail="", raw=raw, recognized=True)
    for pattern, category in _SPECIAL_PERMISSION_PREFIX_PATTERNS:
        match = pattern.match(raw)
        if match:
            remainder = raw[match.end():]
            detail = remainder.strip(" _-:()").replace("_", " ").strip()
            if category in _PRODUCT_PROFILE_ADMIN_CATEGORIES:
                detail = _drop_redundant_slug_suffix(detail)
            return SpecialPermission(category=category, detail=detail, raw=raw, recognized=True)
    generic_match = _GENERIC_ADMIN_PATTERN.match(raw)
    if generic_match:
        remainder = raw[generic_match.end():]
        detail = remainder.strip(" _-:()").replace("_", " ").strip()
        category = "Profile Administrator"
        if known_group_names and detail.strip().casefold() in known_group_names:
            category = "User Group Administrator"
        return SpecialPermission(category=category, detail=detail, raw=raw, recognized=True)
    return SpecialPermission(category=raw, detail="", raw=raw, recognized=False)


def describe_special_permission(name: str, *, known_group_names: set[str] | None = None) -> str:
    """Single-line friendly label — classify_special_permission()'s category
    plus its detail in parentheses, if any."""
    result = classify_special_permission(name, known_group_names=known_group_names)
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
