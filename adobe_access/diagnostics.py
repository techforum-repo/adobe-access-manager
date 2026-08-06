from __future__ import annotations

"""Data assembly for the Diagnostics page. No UI code lives here."""

import json
import platform
import sys
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import streamlit as st

from . import __version__
from .client import client
from .config import settings
from .database import (
    catalog_status,
    last_connection_check,
    record_connection_check,
    sqlite_health,
    table_counts,
)
from .logging_setup import LOG_PATH
from .provisioning import run


def environment_info() -> dict[str, Any]:
    status = catalog_status()
    mode = "Mock" if settings.mock_adobe else ("Live write" if settings.adobe_write_enabled else "Live read/test")
    return {
        "version": __version__,
        "app_env": settings.app_env,
        "mode": mode,
        "adobe_configured": settings.adobe_configured,
        "adobe_org_id": settings.adobe_org_id or "(not set)",
        "adobe_umapi_base_url": settings.adobe_umapi_base_url,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "streamlit_version": st.__version__,
        "pandas_version": pd.__version__,
        "cached_groups": status["group_count"],
        "last_group_sync": status["synced_at"],
    }


def check_adobe_connection() -> dict[str, Any]:
    """Manual, on-demand Adobe connectivity check. Persists the result for the dashboard card."""
    mode = "mock" if settings.mock_adobe else "live"
    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        result = run(client.test_connection())
        record_connection_check(True, mode, json.dumps(result))
        return {"success": True, "mode": mode, "detail": result, "checked_at": checked_at}
    except Exception as exc:
        record_connection_check(False, mode, str(exc))
        return {"success": False, "mode": mode, "detail": str(exc), "checked_at": checked_at}


def log_tail(max_lines: int = 500) -> str:
    if not LOG_PATH.exists():
        return ""
    lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max_lines:])


def diagnostics_bundle() -> str:
    """A single downloadable JSON snapshot: environment + health + recent log tail."""
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": environment_info(),
        "sqlite": sqlite_health(),
        "table_counts": table_counts(),
        "last_connection_check": last_connection_check(),
        "log_tail": log_tail(500).splitlines()[-200:],
    }
    return json.dumps(payload, indent=2, default=str)
