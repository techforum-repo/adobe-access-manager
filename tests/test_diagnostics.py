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


def test_check_adobe_connection_persists_result(temp_db):
    result = diagnostics.check_adobe_connection()
    assert result["success"] is True  # mock client always succeeds
    stored = database.last_connection_check()
    assert stored["success"] is True
