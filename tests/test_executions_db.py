from __future__ import annotations

import pytest

from adobe_access import database


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "executions.db")
    database.initialize()
    return database.DB_PATH


def _results(*, failed=0, succeeded=1):
    rows = []
    for i in range(succeeded):
        rows.append({
            "email": f"user{i}@example.com", "success": True, "created": i == 0,
            "groups_added": ["G1"] if i == 0 else [], "already_assigned": [] if i == 0 else ["G1"],
            "retries": 1 if i == 0 else 0, "error": "",
        })
    for i in range(failed):
        rows.append({
            "email": f"bad{i}@example.com", "success": False, "created": False,
            "groups_added": [], "already_assigned": [], "retries": 0, "error": "boom",
        })
    return rows


def test_save_execution_computes_counts_and_status(temp_db):
    request_id = database.save_recent_request("actor@example.com", [], ["G1"], "Preview", {})
    execution_id = database.save_execution(
        request_id, "actor@example.com",
        "2026-08-05T10:00:00+00:00", "2026-08-05T10:00:02+00:00",
        test_only=False, results=_results(succeeded=2, failed=0),
    )
    execution = database.get_execution(execution_id)
    assert execution["status"] == "Succeeded"
    assert execution["created_count"] == 1
    assert execution["existing_count"] == 1
    assert execution["groups_added_count"] == 1
    assert execution["already_assigned_count"] == 1
    assert execution["retry_count_total"] == 1
    assert execution["duration_ms"] == 2000
    assert len(execution["results"]) == 2


def test_save_execution_marks_partial_when_some_fail(temp_db):
    request_id = database.save_recent_request("actor@example.com", [], ["G1"], "Preview", {})
    execution_id = database.save_execution(
        request_id, "actor@example.com",
        "2026-08-05T10:00:00+00:00", "2026-08-05T10:00:01+00:00",
        test_only=False, results=_results(succeeded=1, failed=1),
    )
    execution = database.get_execution(execution_id)
    assert execution["status"] == "Partial"
    assert execution["failed_count"] == 1


def test_save_execution_marks_failed_when_all_fail(temp_db):
    request_id = database.save_recent_request("actor@example.com", [], ["G1"], "Preview", {})
    execution_id = database.save_execution(
        request_id, "actor@example.com",
        "2026-08-05T10:00:00+00:00", "2026-08-05T10:00:01+00:00",
        test_only=False, results=_results(succeeded=0, failed=2),
    )
    execution = database.get_execution(execution_id)
    assert execution["status"] == "Failed"


def test_list_executions_for_request_orders_newest_first(temp_db):
    request_id = database.save_recent_request("actor@example.com", [], ["G1"], "Preview", {})
    first = database.save_execution(request_id, "a@example.com", "2026-08-05T10:00:00+00:00", "2026-08-05T10:00:01+00:00", True, _results())
    second = database.save_execution(request_id, "a@example.com", "2026-08-05T10:05:00+00:00", "2026-08-05T10:05:01+00:00", False, _results())
    df = database.list_executions_for_request(request_id)
    assert df["id"].tolist() == [second, first]


def test_update_request_status(temp_db):
    request_id = database.save_recent_request("actor@example.com", [], ["G1"], "Preview", {})
    database.update_request_status(request_id, "Executed")
    request = database.get_recent_request(request_id)
    assert request["status"] == "Executed"


def test_get_execution_returns_none_for_missing_id(temp_db):
    assert database.get_execution(999) is None
