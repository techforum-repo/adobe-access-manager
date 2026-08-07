from __future__ import annotations

import pandas as pd
import pytest

from adobe_access import database, provisioning
from adobe_access.provisioning import build_user_table, execute, execution_summary, extract_emails_from_first_column


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    # build_user_table() reads allowed_email_domains through settings_store,
    # which needs the app_settings table to exist.
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "execute.db")
    database.initialize()
    return database.DB_PATH


@pytest.fixture(autouse=True)
def _reset_mock_users():
    # provisioning.client is the shared MockAdobeClient singleton in test env
    # (MOCK_ADOBE defaults to true). Reset it so tests don't leak state.
    provisioning.client.users.clear()
    provisioning.client.users["existing.user@example.com"] = {
        "email": "existing.user@example.com", "first_name": "Existing", "last_name": "User",
        "identity_type": "federatedID", "status": "active", "groups": {"AEM-PROD-AUTHORS"},
    }
    yield
    provisioning.client.users.clear()


def test_build_user_table_excludes_a_non_firstname_lastname_email():
    users = build_user_table(["newperson@example.com"])
    row = users.iloc[0]
    assert row["validation"] == "Invalid"
    assert bool(row["include"]) is False
    assert "firstname.lastname" in row["notes"]


def test_extract_emails_from_first_column_ignores_other_columns():
    df = pd.DataFrame({
        0: ["john.doe@example.com", "jane.smith@example.com"],
        1: ["Sales", "Marketing"],
    })
    assert extract_emails_from_first_column(df) == ["john.doe@example.com", "jane.smith@example.com"]


def test_extract_emails_from_first_column_does_not_require_or_skip_a_header_row():
    """No header row is required — but if a file happens to have one, that row's
    text just becomes one more value here; build_user_table()'s validate_email()
    naturally rejects it rather than this needing to guess it's a header."""
    with_header = pd.DataFrame({0: ["Email", "john.doe@example.com"]})
    assert extract_emails_from_first_column(with_header) == ["Email", "john.doe@example.com"]

    no_header = pd.DataFrame({0: ["john.doe@example.com", "jane.smith@example.com"]})
    assert extract_emails_from_first_column(no_header) == ["john.doe@example.com", "jane.smith@example.com"]


def test_extract_emails_from_first_column_drops_blank_cells():
    df = pd.DataFrame({0: ["john.doe@example.com", None, "jane.smith@example.com"]})
    assert extract_emails_from_first_column(df) == ["john.doe@example.com", "jane.smith@example.com"]


def test_extract_emails_from_first_column_handles_an_empty_dataframe():
    assert extract_emails_from_first_column(pd.DataFrame()) == []


def test_execute_creates_new_user_and_adds_groups():
    users = build_user_table(["new.person@example.com"])
    result = execute(users, ["AEM-PROD-AUTHORS"], test_only=False)
    assert len(result) == 1
    row = result.iloc[0]
    assert bool(row["success"]) is True
    assert bool(row["created"]) is True
    assert row["groups_added"] == ["AEM-PROD-AUTHORS"]
    assert row["already_assigned"] == []
    assert row["retries"] == 0


def test_execute_is_idempotent_on_rerun():
    users = build_user_table(["new.person@example.com"])
    first = execute(users, ["AEM-PROD-AUTHORS"], test_only=False)
    assert bool(first.iloc[0]["created"]) is True
    assert first.iloc[0]["groups_added"] == ["AEM-PROD-AUTHORS"]

    second = execute(users, ["AEM-PROD-AUTHORS"], test_only=False)
    row = second.iloc[0]
    assert bool(row["success"]) is True
    assert bool(row["created"]) is False  # user already exists now
    assert row["groups_added"] == []  # nothing new to add
    assert row["already_assigned"] == ["AEM-PROD-AUTHORS"]


def test_execute_skips_group_already_assigned_to_existing_user():
    users = build_user_table(["existing.user@example.com"])
    result = execute(users, ["AEM-PROD-AUTHORS"], test_only=False)
    row = result.iloc[0]
    assert bool(row["created"]) is False
    assert row["groups_added"] == []
    assert row["already_assigned"] == ["AEM-PROD-AUTHORS"]


def test_execute_retries_transient_failure_then_succeeds(monkeypatch):
    calls = {"n": 0}
    real_provision = provisioning.client.provision

    async def flaky_provision(email, first_name, last_name, groups, test_only):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("Adobe request timed out. Endpoint: https://x")
        return await real_provision(email, first_name, last_name, groups, test_only)

    monkeypatch.setattr(provisioning.client, "provision", flaky_provision)
    sleeps = []
    monkeypatch.setattr("adobe_access.retry.time.sleep", sleeps.append)

    users = build_user_table(["new.person@example.com"])
    result = execute(users, ["AEM-PROD-AUTHORS"], test_only=False)
    row = result.iloc[0]
    assert bool(row["success"]) is True
    assert row["retries"] == 1
    assert len(sleeps) == 1


def test_execute_does_not_retry_permanent_failure(monkeypatch):
    calls = {"n": 0}

    async def always_forbidden(email, first_name, last_name, groups, test_only):
        calls["n"] += 1
        raise RuntimeError("Adobe returned HTTP 403: forbidden")

    monkeypatch.setattr(provisioning.client, "provision", always_forbidden)
    users = build_user_table(["new.person@example.com"])
    result = execute(users, ["AEM-PROD-AUTHORS"], test_only=False)
    row = result.iloc[0]
    assert bool(row["success"]) is False
    assert calls["n"] == 1
    assert "403" in row["error"] or "forbidden" in row["error"].lower()


def test_execution_summary_counts_created_existing_failed_and_retries():
    results = pd.DataFrame([
        {"email": "a@example.com", "success": True, "created": True, "groups_added": ["G1", "G2"], "already_assigned": [], "retries": 1, "error": ""},
        {"email": "b@example.com", "success": True, "created": False, "groups_added": [], "already_assigned": ["G1"], "retries": 0, "error": ""},
        {"email": "c@example.com", "success": False, "created": False, "groups_added": [], "already_assigned": [], "retries": 2, "error": "boom"},
    ])
    summary = execution_summary(results)
    assert summary == {
        "created": 1, "existing": 1, "groups_added": 2,
        "already_assigned": 1, "failed": 1, "retries": 3,
    }


def test_execution_summary_handles_empty_results():
    assert execution_summary(pd.DataFrame()) == {
        "created": 0, "existing": 0, "groups_added": 0,
        "already_assigned": 0, "failed": 0, "retries": 0,
    }
