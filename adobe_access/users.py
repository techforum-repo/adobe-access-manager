from __future__ import annotations

import re
from typing import Any

import pandas as pd

from .client import client
from .database import read_managed_groups
from .provisioning import run

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class UserLookupError(ValueError):
    """Raised when a user lookup request is invalid or Adobe cannot be reached."""


def normalize_lookup_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise UserLookupError("Enter a complete email address, for example firstname.lastname@example.com.")
    return email


def lookup_user(email: str) -> dict[str, Any] | None:
    """Perform an exact Adobe user lookup and normalize membership presentation."""
    normalized = normalize_lookup_email(email)
    try:
        user = run(client.get_user(normalized))
    except Exception as exc:  # Keep UI/service error consistent without leaking stack traces.
        raise UserLookupError(str(exc)) from exc
    if not user:
        return None
    result = dict(user)
    result["email"] = str(result.get("email") or normalized).lower()
    result["groups"] = set(result.get("groups") or set())
    result["display_name"] = (
        f"{result.get('first_name', '')} {result.get('last_name', '')}".strip()
        or result["email"]
    )
    return result


def membership_table(user: dict[str, Any], managed_groups: pd.DataFrame | None = None) -> pd.DataFrame:
    """Return only memberships present in the synchronized custom user-group cache.

    `.attrs["ignored_non_custom_memberships"]` on the result reports how many of
    the user's real Adobe memberships were excluded because they're not in the
    local custom-group cache (product profiles, admin groups, groups that haven't
    been synced, ...) — context that this table is a subset, not the user's full
    Adobe membership list.
    """
    cached = read_managed_groups() if managed_groups is None else managed_groups.copy()
    memberships = sorted(set(user.get("groups") or set()), key=str.casefold)
    columns = ["display_name", "system", "adobe_group_name", "cached"]

    def _finalize(frame: pd.DataFrame, ignored: int) -> pd.DataFrame:
        frame.attrs["ignored_non_custom_memberships"] = ignored
        return frame

    if not memberships:
        return _finalize(pd.DataFrame(columns=columns), 0)
    if cached.empty or "adobe_group_name" not in cached.columns:
        return _finalize(pd.DataFrame(columns=columns), len(memberships))

    metadata: dict[str, dict[str, Any]] = {}
    canonical_names: dict[str, str] = {}
    for _, row in cached.iterrows():
        group_name = str(row.get("adobe_group_name") or "").strip()
        if not group_name:
            continue
        key = group_name.casefold()
        metadata[key] = row.to_dict()
        canonical_names[key] = group_name

    rows: list[dict[str, Any]] = []
    ignored = 0
    for returned_name in memberships:
        key = str(returned_name).strip().casefold()
        item = metadata.get(key)
        if item is None:
            ignored += 1
            continue
        canonical_name = canonical_names[key]
        rows.append({
            "display_name": item.get("display_name") or canonical_name,
            "system": item.get("system") or "Other",
            "adobe_group_name": canonical_name,
            "cached": True,
        })

    if not rows:
        return _finalize(pd.DataFrame(columns=columns), ignored)
    result = pd.DataFrame(rows, columns=columns).sort_values(
        ["system", "display_name", "adobe_group_name"],
        key=lambda col: col.astype(str).str.casefold(),
    ).reset_index(drop=True)
    return _finalize(result, ignored)


def user_export_table(user: dict[str, Any], memberships: pd.DataFrame) -> pd.DataFrame:
    """Build a flat CSV-friendly export of user details and memberships."""
    if memberships.empty:
        return pd.DataFrame([{
            "email": user.get("email", ""),
            "first_name": user.get("first_name", ""),
            "last_name": user.get("last_name", ""),
            "identity_type": user.get("identity_type", ""),
            "status": user.get("status", ""),
            "group_display_name": "",
            "system": "",
            "adobe_group_name": "",
        }])

    rows = []
    for _, membership in memberships.iterrows():
        rows.append({
            "email": user.get("email", ""),
            "first_name": user.get("first_name", ""),
            "last_name": user.get("last_name", ""),
            "identity_type": user.get("identity_type", ""),
            "status": user.get("status", ""),
            "group_display_name": membership["display_name"],
            "system": membership["system"],
            "adobe_group_name": membership["adobe_group_name"],
        })
    return pd.DataFrame(rows)


def compare_custom_group_memberships(
    left_user: dict[str, Any],
    right_user: dict[str, Any],
    managed_groups: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Compare only memberships present in the synchronized custom-group cache."""
    cached = read_managed_groups() if managed_groups is None else managed_groups.copy()
    columns = [
        "display_name",
        "system",
        "adobe_group_name",
        "left_member",
        "right_member",
        "comparison",
    ]
    if cached.empty or "adobe_group_name" not in cached.columns:
        return pd.DataFrame(columns=columns)

    left_keys = {str(value).strip().casefold() for value in (left_user.get("groups") or set())}
    right_keys = {str(value).strip().casefold() for value in (right_user.get("groups") or set())}
    rows: list[dict[str, Any]] = []
    for _, group in cached.iterrows():
        adobe_name = str(group.get("adobe_group_name") or "").strip()
        if not adobe_name:
            continue
        key = adobe_name.casefold()
        left_member = key in left_keys
        right_member = key in right_keys
        if not left_member and not right_member:
            continue
        comparison = "Shared" if left_member and right_member else ("Only first user" if left_member else "Only second user")
        rows.append({
            "display_name": group.get("display_name") or adobe_name,
            "system": group.get("system") or "Other",
            "adobe_group_name": adobe_name,
            "left_member": left_member,
            "right_member": right_member,
            "comparison": comparison,
        })
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["system", "display_name", "adobe_group_name"],
        key=lambda col: col.astype(str).str.casefold(),
    ).reset_index(drop=True)


def build_copy_access_preview(
    source_user: dict[str, Any],
    target_users: list[dict[str, Any] | None],
    target_emails: list[str],
    selected_group_names: list[str],
    managed_groups: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build a read-only per-target preview for copying selected custom groups."""
    cached = read_managed_groups() if managed_groups is None else managed_groups.copy()
    columns = [
        "email", "target_status", "group_display_name", "system",
        "adobe_group_name", "membership_status", "will_add",
    ]
    if cached.empty or not selected_group_names:
        return pd.DataFrame(columns=columns)

    metadata = {}
    for _, row in cached.iterrows():
        name = str(row.get("adobe_group_name") or "").strip()
        if name:
            metadata[name.casefold()] = row.to_dict()

    rows = []
    for email, target in zip(target_emails, target_users):
        existing = target is not None
        current = {str(v).strip().casefold() for v in ((target or {}).get("groups") or set())}
        for group_name in selected_group_names:
            key = str(group_name).strip().casefold()
            item = metadata.get(key)
            if not item:
                continue
            already = key in current
            rows.append({
                "email": email,
                "target_status": "Existing" if existing else "New user",
                "group_display_name": item.get("display_name") or item.get("adobe_group_name"),
                "system": item.get("system") or "Other",
                "adobe_group_name": item.get("adobe_group_name"),
                "membership_status": "Already assigned" if already else "Will add",
                "will_add": not already,
            })
    return pd.DataFrame(rows, columns=columns)
