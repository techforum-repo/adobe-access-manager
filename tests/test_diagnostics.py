from __future__ import annotations

import json

import pytest

from adobe_access import database, diagnostics


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "diagnostics.db")
    database.initialize()
    return database.DB_PATH


def test_connection_check_round_trips(temp_db):
    assert database.last_connection_check() is None
    database.record_connection_check(True, "mock", "ok")
    result = database.last_connection_check()
    assert result["success"] is True
    assert result["mode"] == "mock"
    database.record_connection_check(False, "live", "boom")
    result = database.last_connection_check()
    assert result["success"] is False
    assert result["detail"] == "boom"


def test_sqlite_health_reports_ok_on_a_fresh_db(temp_db):
    health = database.sqlite_health()
    assert health["ok"] is True
    assert health["integrity"].lower() == "ok"
    assert health["size_bytes"] > 0


def test_table_counts_reflects_inserted_rows(temp_db):
    database.record("actor@example.com", "test-action", "user@example.com", [], "Success")
    counts = database.table_counts()
    assert counts["audit_events"] == 1


def test_requests_today_failed_count_only_counts_failures(temp_db):
    database.save_recent_request(
        "actor@example.com", [{"email": "a@example.com"}], ["G1"], "Preview",
        {"users": 1, "failures": 0},
    )
    assert database.requests_today_failed_count() == 0
    database.save_recent_request(
        "actor@example.com", [{"email": "b@example.com"}], ["G1"], "Preview",
        {"users": 1, "failures": 1},
    )
    assert database.requests_today_failed_count() == 1


def test_most_used_templates_orders_by_usage(temp_db):
    for _ in range(3):
        database.save_recent_request("a@example.com", [], ["G1"], "Preview", {}, 1, "CJA Analyst")
    database.save_recent_request("a@example.com", [], ["G1"], "Preview", {}, 2, "AEM Author")
    top = database.most_used_templates(5)
    assert top.iloc[0]["template_name"] == "CJA Analyst"
    assert top.iloc[0]["uses"] == 3


def test_diagnostics_bundle_is_valid_json(temp_db):
    payload = json.loads(diagnostics.diagnostics_bundle())
    assert "environment" in payload
    assert "sqlite" in payload
    assert "table_counts" in payload


def test_diagnostics_bundle_reuses_precomputed_pieces_instead_of_requerying(temp_db):
    """The Diagnostics page already computes env/sqlite/counts/log once for its
    own always-visible display — passing them through must skip re-running the
    same (sqlite_health() = a full PRAGMA integrity_check, table_counts() = 9
    COUNT(*) queries) work a second time just to build the download bundle."""
    sentinel_counts = {"audit_events": 999}
    payload = json.loads(diagnostics.diagnostics_bundle(
        environment={"fake": "env"},
        sqlite={"fake": "sqlite"},
        counts=sentinel_counts,
        connection={"fake": "connection"},
        log_lines=["line one", "line two"],
    ))
    assert payload["environment"] == {"fake": "env"}
    assert payload["sqlite"] == {"fake": "sqlite"}
    assert payload["table_counts"] == sentinel_counts
    assert payload["last_connection_check"] == {"fake": "connection"}
    assert payload["log_tail"] == ["line one", "line two"]


def test_diagnostics_page_computes_table_counts_exactly_once(temp_db, monkeypatch):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    from adobe_access.ui import diagnostics_page

    calls = {"n": 0}
    real_table_counts = database.table_counts

    def counting_table_counts():
        calls["n"] += 1
        return real_table_counts()

    # `from adobe_access.database import table_counts` in both diagnostics.py
    # and diagnostics_page.py binds its own name at import time — patch both,
    # since a regression (diagnostics_bundle() re-querying instead of reusing
    # what's passed in) would show up as a second call through diagnostics.py's
    # binding, not database.py's.
    monkeypatch.setattr(diagnostics_page, "table_counts", counting_table_counts)
    monkeypatch.setattr(diagnostics, "table_counts", counting_table_counts)

    app_path = str(Path(__file__).resolve().parent.parent / "app.py")
    at = AppTest.from_file(app_path)
    at.run(timeout=30)
    at.radio(key="navigation").set_value("Diagnostics").run(timeout=30)
    assert not at.exception
    assert calls["n"] == 1, "table_counts() should be computed once per page render, not once for display and again for the bundle"


def test_check_adobe_connection_persists_result(temp_db):
    result = diagnostics.check_adobe_connection()
    assert result["success"] is True  # mock client always succeeds
    stored = database.last_connection_check()
    assert stored["success"] is True
