from __future__ import annotations

"""Audit history / Request history: previously had no test coverage at all.
Covers the "silently capped" fix — both pages hard-limit how many rows they
load, and now say so instead of quietly hiding older history."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from adobe_access import database
from adobe_access.ui import audit_history, request_history

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "history_pages.db")
    database.initialize()
    return database.DB_PATH


def _goto(at: AppTest, page: str) -> None:
    at.radio(key="navigation").set_value(page).run(timeout=30)
    assert not at.exception, (page, list(at.exception))


def test_audit_history_does_not_mention_a_cap_when_under_the_limit(temp_db, monkeypatch):
    monkeypatch.setattr(audit_history, "_DISPLAY_LIMIT", 5)
    for i in range(3):
        database.record("actor@example.com", "test-action", f"user{i}@example.com", [], "Success", "")

    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "Audit history")
    assert not any("total audit events" in c.value for c in at.caption)


def test_audit_history_says_so_when_capped(temp_db, monkeypatch):
    monkeypatch.setattr(audit_history, "_DISPLAY_LIMIT", 5)
    for i in range(8):
        database.record("actor@example.com", "test-action", f"user{i}@example.com", [], "Success", "")

    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "Audit history")
    matches = [c.value for c in at.caption if "total audit events" in c.value]
    assert matches, "expected a caption explaining the view is capped"
    assert "most recent 5 of 8" in matches[0]


def test_request_history_says_so_when_capped(temp_db, monkeypatch):
    monkeypatch.setattr(request_history, "_DISPLAY_LIMIT", 3)
    for i in range(5):
        database.save_recent_request(
            "actor@example.com", [{"email": f"user{i}@example.com"}], ["AEM-PROD-AUTHORS"],
            "Preview", {"users": 1, "existing": 0, "new": 1, "assignments": 1, "already": 0, "failures": 0},
        )

    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "Request history")
    matches = [c.value for c in at.caption if "total requests" in c.value]
    assert matches, "expected a caption explaining the view is capped"
    assert "most recent 3 of 5" in matches[0]


def test_request_history_does_not_mention_a_cap_when_under_the_limit(temp_db, monkeypatch):
    monkeypatch.setattr(request_history, "_DISPLAY_LIMIT", 10)
    database.save_recent_request(
        "actor@example.com", [{"email": "user@example.com"}], ["AEM-PROD-AUTHORS"],
        "Preview", {"users": 1, "existing": 0, "new": 1, "assignments": 1, "already": 0, "failures": 0},
    )

    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "Request history")
    assert not any("total requests" in c.value for c in at.caption)
