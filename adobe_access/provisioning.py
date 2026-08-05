from __future__ import annotations

import asyncio
from typing import Any

import pandas as pd

from .client import client
from .config import settings
from .utils import derive_name, normalize_email, validate_email


def run(coro):
    return asyncio.run(coro)


def build_user_table(emails: list[str]) -> pd.DataFrame:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for raw in emails:
        email = normalize_email(raw)
        duplicate = email in seen
        seen.add(email)
        valid, note = validate_email(email, settings.allowed_domains)
        parsed = derive_name(email)
        rows.append({
            "include": valid and not duplicate,
            "email": email,
            "first_name": parsed.first_name,
            "last_name": parsed.last_name,
            "validation": "Duplicate" if duplicate else ("Valid" if valid else "Invalid"),
            "notes": "Duplicate input" if duplicate else note or ("Review derived name" if parsed.ambiguous else ""),
        })
    return pd.DataFrame(rows)


def preview(users: pd.DataFrame, groups: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    included = users[users["include"] == True]  # noqa: E712
    for _, row in included.iterrows():
        email = str(row["email"])
        try:
            existing = run(client.get_user(email))
            current = set(existing.get("groups", set())) if existing else set()
            missing = [g for g in groups if g not in current]
            already = sorted(current.intersection(groups))
            rows.append({
                "email": email,
                "name": f"{row.get('first_name','')} {row.get('last_name','')}".strip(),
                "exists": bool(existing),
                "user_action": "Use existing" if existing else "Create user",
                "current_groups": "; ".join(sorted(current)) or "None",
                "groups_to_add": "; ".join(missing) or "None",
                "already_assigned": "; ".join(already) or "None",
                "ready": bool(missing or not existing),
                "lookup": "OK",
            })
        except Exception as exc:
            rows.append({
                "email": email,
                "name": f"{row.get('first_name','')} {row.get('last_name','')}".strip(),
                "exists": False,
                "user_action": "Lookup failed",
                "current_groups": "Not evaluated",
                "groups_to_add": "Not evaluated",
                "already_assigned": "Not evaluated",
                "ready": False,
                "lookup": str(exc),
            })
    return pd.DataFrame(rows)


def compare_users(left: dict[str, Any], right: dict[str, Any]) -> pd.DataFrame:
    left_groups = set(left.get("groups", set()))
    right_groups = set(right.get("groups", set()))
    rows = []
    for group in sorted(left_groups | right_groups):
        rows.append({
            "group": group,
            "left": group in left_groups,
            "right": group in right_groups,
            "difference": "Same" if (group in left_groups) == (group in right_groups) else "Different",
        })
    return pd.DataFrame(rows)


def validate_users_against_adobe(users: pd.DataFrame) -> pd.DataFrame:
    """Add read-only Adobe existence and membership information to locally validated rows."""
    output = users.copy()
    for column, default in {
        "adobe_status": "Not checked",
        "current_group_count": 0,
        "lookup_details": "",
    }.items():
        if column not in output.columns:
            output[column] = default
    for index, row in output.iterrows():
        if str(row.get("validation")) != "Valid" or not bool(row.get("include")):
            output.at[index, "adobe_status"] = "Skipped"
            continue
        email = str(row.get("email") or "")
        try:
            existing = run(client.get_user(email))
            if existing:
                groups = existing.get("groups", set()) or set()
                output.at[index, "adobe_status"] = "Existing"
                output.at[index, "current_group_count"] = len(groups)
            else:
                output.at[index, "adobe_status"] = "New"
                output.at[index, "current_group_count"] = 0
            output.at[index, "lookup_details"] = ""
        except Exception as exc:
            output.at[index, "adobe_status"] = "Lookup failed"
            output.at[index, "lookup_details"] = str(exc)
    return output


def preview_summary(preview_df: pd.DataFrame) -> dict[str, int]:
    if preview_df.empty:
        return {"users": 0, "existing": 0, "new": 0, "assignments": 0, "already": 0, "failures": 0}
    existing = int(preview_df.get("exists", pd.Series(dtype=bool)).fillna(False).sum())
    users = len(preview_df)
    assignments = 0
    already = 0
    for value in preview_df.get("groups_to_add", pd.Series(dtype=str)).fillna(""):
        if value and value != "None" and value != "Not evaluated":
            assignments += len([item for item in str(value).split(";") if item.strip()])
    for value in preview_df.get("already_assigned", pd.Series(dtype=str)).fillna(""):
        if value and value != "None" and value != "Not evaluated":
            already += len([item for item in str(value).split(";") if item.strip()])
    failures = int((preview_df.get("lookup", pd.Series(dtype=str)) != "OK").sum())
    return {
        "users": users,
        "existing": existing,
        "new": users - existing - failures,
        "assignments": assignments,
        "already": already,
        "failures": failures,
    }
