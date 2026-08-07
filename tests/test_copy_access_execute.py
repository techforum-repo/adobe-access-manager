from __future__ import annotations

"""Copy access previously had no way to actually apply a copy — preview only.
Covers the new Run test / Execute capability and per-group removal from the
final preview, end to end via AppTest against the real page."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from adobe_access import database, provisioning
from adobe_access.config import settings

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "copy_access_execute.db")
    database.initialize()
    return database.DB_PATH


@pytest.fixture(autouse=True)
def _seed_mock_users():
    provisioning.client.users.clear()
    provisioning.client.users["source.user@example.com"] = {
        "email": "source.user@example.com", "first_name": "Source", "last_name": "User",
        "identity_type": "federatedID", "status": "active",
        "groups": {"AEM-PROD-AUTHORS", "AEM-DEV-DEVELOPERS"},
    }
    yield
    provisioning.client.users.clear()


def _goto(at: AppTest, page: str) -> None:
    at.radio(key="navigation").set_value(page).run(timeout=30)
    assert not at.exception, (page, list(at.exception))


def _sync_groups(at: AppTest) -> None:
    _goto(at, "User groups")
    [b for b in at.button if b.label == "Sync from Adobe"][0].click().run(timeout=30)
    assert not at.exception


def _build_preview(at: AppTest, target_email: str = "new.target@example.com") -> None:
    _goto(at, "Copy access")
    [w for w in at.text_input if w.label == "Source user email"][0].set_value("source.user@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Load source user"][0].click().run(timeout=30)
    [w for w in at.text_area if w.label == "Target users"][0].set_value(target_email).run(timeout=30)
    [b for b in at.button if b.label == "Build copy preview"][0].click().run(timeout=30)
    assert not at.exception


def test_execute_is_disabled_without_the_write_flag(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)
    _build_preview(at)

    assert not [b for b in at.button if "Execute" in b.label]
    assert any("Execute is disabled" in i.value for i in at.info)


def test_run_test_reports_per_target_results_without_changing_anything(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)
    _build_preview(at)

    [b for b in at.button if b.label == "Run test"][0].click().run(timeout=30)
    assert not at.exception
    assert "new.target@example.com" not in provisioning.client.users, "Run test must never create a real user"
    tables = [df.value for df in at.dataframe if "status" in getattr(df.value, "columns", [])]
    assert tables, "expected a test-results table"
    assert tables[0].iloc[0]["status"] == "Test passed"


def test_execute_creates_the_target_and_adds_the_copied_groups(temp_db, monkeypatch):
    monkeypatch.setattr(settings, "adobe_write_enabled", True)
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)
    _build_preview(at)

    [w for w in at.checkbox if "I confirm this will make real changes" in w.label][0].set_value(True).run(timeout=30)
    [b for b in at.button if "Execute" in b.label][0].click().run(timeout=30)
    assert not at.exception

    created = provisioning.client.users.get("new.target@example.com")
    assert created is not None, "target user should have been created in Adobe"
    assert created["groups"] >= {"AEM-PROD-AUTHORS", "AEM-DEV-DEVELOPERS"}
    assert any(s.value.startswith("Execution #") for s in at.success)


def test_execute_is_idempotent_on_rerun(temp_db, monkeypatch):
    monkeypatch.setattr(settings, "adobe_write_enabled", True)
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)
    _build_preview(at)

    [w for w in at.checkbox if "I confirm this will make real changes" in w.label][0].set_value(True).run(timeout=30)
    [b for b in at.button if "Execute" in b.label][0].click().run(timeout=30)
    assert not at.exception

    # Re-confirm (a fresh run's checkbox state) and execute again — must not error
    # or duplicate group assignments; MockAdobeClient.provision() already dedupes
    # via a set(), this just confirms the UI flow supports a safe re-run.
    [w for w in at.checkbox if "I confirm this will make real changes" in w.label][0].set_value(True).run(timeout=30)
    [b for b in at.button if "Execute" in b.label][0].click().run(timeout=30)
    assert not at.exception
    assert provisioning.client.users["new.target@example.com"]["groups"] >= {"AEM-PROD-AUTHORS", "AEM-DEV-DEVELOPERS"}


def test_removing_a_group_excludes_it_from_the_metrics_and_execution(temp_db, monkeypatch):
    monkeypatch.setattr(settings, "adobe_write_enabled", True)
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)
    _build_preview(at)

    assert [b for b in at.button if b.key == "copy_remove_group_AEM-DEV-DEVELOPERS"]
    [b for b in at.button if b.key == "copy_remove_group_AEM-DEV-DEVELOPERS"][0].click().run(timeout=30)
    assert not at.exception

    additions_metric = [m for m in at.metric if m.label == "Memberships to add"][0]
    assert additions_metric.value == "1", "removed group must not count toward what would be added"

    [w for w in at.checkbox if "I confirm this will make real changes" in w.label][0].set_value(True).run(timeout=30)
    [b for b in at.button if "Execute" in b.label][0].click().run(timeout=30)
    assert not at.exception
    created = provisioning.client.users["new.target@example.com"]
    assert "AEM-PROD-AUTHORS" in created["groups"]
    assert "AEM-DEV-DEVELOPERS" not in created["groups"], "removed group must not be applied"


def test_removed_group_can_be_restored(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)
    _build_preview(at)

    [b for b in at.button if b.key == "copy_remove_group_AEM-DEV-DEVELOPERS"][0].click().run(timeout=30)
    assert [b for b in at.button if b.key == "copy_restore_group_AEM-DEV-DEVELOPERS"]
    [b for b in at.button if b.key == "copy_restore_group_AEM-DEV-DEVELOPERS"][0].click().run(timeout=30)
    assert not at.exception
    additions_metric = [m for m in at.metric if m.label == "Memberships to add"][0]
    assert additions_metric.value == "2", "restoring the group should bring the count back"


def test_removing_every_group_disables_test_and_execute(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)
    _build_preview(at)

    for name in ["AEM-PROD-AUTHORS", "AEM-DEV-DEVELOPERS"]:
        [b for b in at.button if b.key == f"copy_remove_group_{name}"][0].click().run(timeout=30)
    assert not at.exception
    assert not [b for b in at.button if b.label == "Run test"]
    assert any("nothing left to test or execute" in w.value for w in at.warning)


def test_start_over_resets_the_page(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)
    _build_preview(at)

    [b for b in at.button if b.label == "Start over"][0].click().run(timeout=30)
    assert not at.exception
    assert at.session_state["copy_source"] is None
    assert at.session_state["copy_preview"].empty
    assert at.session_state["copy_target_text"] == ""
    assert not any("Loaded" in s.value for s in at.success), "should no longer show the previously loaded source"
    assert not [w for w in at.text_area if w.label == "Target users"], "gated content should be gone once source is cleared"
