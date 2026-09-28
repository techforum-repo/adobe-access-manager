from __future__ import annotations

"""UI-wiring coverage for editing an existing user's first/last name from the
User search detail view (both the exact-search and Browse-synced paths)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from adobe_access import database, provisioning
from adobe_access.client import client
from adobe_access.users import update_user_name

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "user_edit_name.db")
    database.initialize()
    return database.DB_PATH


@pytest.fixture(autouse=True)
def _seed_mock_users():
    provisioning.client.users.clear()
    provisioning.client.users["existing.user@example.com"] = {
        "email": "existing.user@example.com", "first_name": "Existing", "last_name": "User",
        "identity_type": "federatedID", "status": "active", "groups": {"AEM-PROD-AUTHORS"},
    }
    provisioning.client.users["adobe.id.user@example.com"] = {
        "email": "adobe.id.user@example.com", "first_name": "Adobe", "last_name": "IdUser",
        "identity_type": "adobeID", "status": "active", "groups": set(),
    }
    yield
    provisioning.client.users.clear()


def _goto(at: AppTest, page: str) -> None:
    at.radio(key="navigation").set_value(page).run(timeout=30)
    assert not at.exception, (page, list(at.exception))


def test_update_user_name_renames_the_mock_user_directly():
    result = update_user_name("existing.user@example.com", "New", "Name")
    assert result["success"] is True
    assert client.users["existing.user@example.com"]["first_name"] == "New"
    assert client.users["existing.user@example.com"]["last_name"] == "Name"


def test_edit_name_from_search_detail_updates_the_displayed_name(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "User search")
    [w for w in at.text_area if w.label == "User email(s)"][0].set_value("existing.user@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Search Adobe"][0].click().run(timeout=30)
    assert not at.exception
    assert any("Existing User" in m.value for m in at.markdown)

    [w for w in at.text_input if w.label == "First name"][0].set_value("Renamed").run(timeout=30)
    [w for w in at.text_input if w.label == "Last name"][0].set_value("Person").run(timeout=30)
    [b for b in at.button if b.label == "Save"][0].click().run(timeout=30)
    assert not at.exception

    assert any("Renamed Person" in m.value for m in at.markdown)
    assert client.users["existing.user@example.com"]["first_name"] == "Renamed"
    assert client.users["existing.user@example.com"]["last_name"] == "Person"


def test_adobe_id_user_cannot_be_renamed(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "User search")
    [w for w in at.text_area if w.label == "User email(s)"][0].set_value("adobe.id.user@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Search Adobe"][0].click().run(timeout=30)
    assert not at.exception

    assert not any(w.label == "First name" for w in at.text_input)
    assert any("can't rename" in c.value for c in at.caption)


def test_edit_name_from_browse_cached_updates_the_local_cache(temp_db):
    database.replace_managed_users([
        {
            "email": "existing.user@example.com", "first_name": "Existing", "last_name": "User",
            "identity_type": "federatedID", "status": "active", "groups": {"AEM-PROD-AUTHORS"},
        }
    ])
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "User search")
    at.tabs[1].run(timeout=30)  # Browse synced users

    [w for w in at.selectbox if w.label == "Pick a cached user"][0].set_value("existing.user@example.com").run(timeout=30)
    assert not at.exception

    [w for w in at.text_input if w.label == "First name"][0].set_value("Renamed").run(timeout=30)
    [w for w in at.text_input if w.label == "Last name"][0].set_value("Person").run(timeout=30)
    [b for b in at.button if b.label == "Save"][0].click().run(timeout=30)
    assert not at.exception

    cached = database.read_managed_users()
    row = cached[cached["email"] == "existing.user@example.com"].iloc[0]
    assert row["first_name"] == "Renamed"
    assert row["last_name"] == "Person"
