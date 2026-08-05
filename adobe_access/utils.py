from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedName:
    first_name: str
    last_name: str
    ambiguous: bool


def normalize_email(value: str) -> str:
    return value.strip().lower()


def derive_name(email: str) -> ParsedName:
    local = email.split("@", 1)[0]
    parts = [p for p in re.split(r"[._-]+", local) if p]
    if not parts:
        return ParsedName("", "", True)
    first = parts[0].title()
    last = " ".join(p.title() for p in parts[1:])
    ambiguous = len(parts) < 2 or any(ch.isdigit() for ch in local)
    return ParsedName(first, last, ambiguous)


def validate_email(email: str, allowed_domains: set[str]) -> tuple[bool, str]:
    email = normalize_email(email)
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        return False, "Invalid email format"
    domain = email.rsplit("@", 1)[1]
    if allowed_domains and domain not in allowed_domains:
        return False, f"Only {', '.join(sorted(allowed_domains))} addresses are allowed"
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
