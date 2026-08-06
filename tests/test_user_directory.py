from __future__ import annotations

"""Tests for the "Browse synced users" feature: the managed_users cache table,
and adobe_access.users.browse_cached_users()/get_cached_user()."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from adobe_access import database, provisioning
from adobe_access.users import browse_cached_users, get_cached_user

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "user_directory.db")
    database.initialize()
    return database.DB_PATH


def _sync(users):
    return database.replace_managed_users(users)


def test_replace_managed_users_dedupes_and_lowercases_email(temp_db):
    result = _sync([
        {"email": "Jane.Doe@example.com", "first_name": "Jane", "last_name": "Doe",
         "identity_type": "federatedID", "status": "active", "groups": {"AEM-PROD-AUTHORS"}},
        {"email": "jane.doe@example.com", "first_name": "Jane", "last_name": "Doe",
         "identity_type": "federatedID", "status": "active", "groups": set()},
    ])
    assert result["users"] == 1
    df = database.read_managed_users()
    assert df["email"].tolist() == ["jane.doe@example.com"]


def test_user_catalog_status_reflects_sync(temp_db):
    assert database.user_catalog_status()["user_count"] == 0
    _sync([{"email": "a@example.com", "groups": set()}])
    status = database.user_catalog_status()
    assert status["user_count"] == 1
    assert status["synced_at"]


def test_browse_cached_users_empty_before_sync(temp_db):
    assert browse_cached_users().empty


def test_browse_cached_users_blank_query_returns_everyone(temp_db):
    _sync([
        {"email": "a@example.com", "first_name": "Alice", "last_name": "Adams", "groups": set()},
        {"email": "b@example.com", "first_name": "Bob", "last_name": "Brown", "groups": set()},
    ])
    result = browse_cached_users("")
    assert sorted(result["email"]) == ["a@example.com", "b@example.com"]


def test_browse_cached_users_filters_by_name_or_email(temp_db):
    _sync([
        {"email": "alice@example.com", "first_name": "Alice", "last_name": "Adams", "groups": set()},
        {"email": "bob@example.com", "first_name": "Bob", "last_name": "Brown", "groups": set()},
    ])
    assert browse_cached_users("alice")["email"].tolist() == ["alice@example.com"]
    assert browse_cached_users("brown")["email"].tolist() == ["bob@example.com"]
    assert browse_cached_users("nobody").empty


def test_browse_cached_users_custom_group_count_reflects_current_group_cache(temp_db):
    database.replace_managed_groups([{"name": "AEM-PROD-AUTHORS", "system": "AEM"}])
    _sync([{
        "email": "a@example.com", "first_name": "A", "last_name": "User",
        "groups": {"AEM-PROD-AUTHORS", "SYSTEM-PROFILE-NOT-CACHED"},
    }])
    row = browse_cached_users().iloc[0]
    assert row["custom_group_count"] == 1  # only the cached custom group counts

    # Re-syncing groups (without re-syncing users) changes the count immediately —
    # it's computed live against the current group cache, not stored at user-sync time.
    database.replace_managed_groups([
        {"name": "AEM-PROD-AUTHORS", "system": "AEM"},
        {"name": "SYSTEM-PROFILE-NOT-CACHED", "system": "Other"},
    ])
    row = browse_cached_users().iloc[0]
    assert row["custom_group_count"] == 2


def test_get_cached_user_returns_lookup_user_compatible_shape(temp_db):
    _sync([{
        "email": "a@example.com", "first_name": "A", "last_name": "User",
        "identity_type": "federatedID", "status": "active", "groups": {"G1"},
    }])
    user = get_cached_user("a@example.com")
    assert user["email"] == "a@example.com"
    assert user["display_name"] == "A User"
    assert user["groups"] == {"G1"}
    assert user["identity_type"] == "federatedID"


def test_get_cached_user_is_case_insensitive_and_missing_returns_none(temp_db):
    _sync([{"email": "a@example.com", "groups": set()}])
    assert get_cached_user("A@EXAMPLE.COM") is not None
    assert get_cached_user("nobody@example.com") is None


# --- End-to-end through the real page -------------------------------------------

def test_sync_and_browse_flow_through_the_app(temp_db):
    # provisioning.client is the shared MockAdobeClient singleton — other test
    # files' fixtures clear its .users dict in teardown, so seed explicitly
    # rather than relying on ambient state (same pattern as test_execute.py).
    provisioning.client.users.clear()
    provisioning.client.users["existing.user@example.com"] = {
        "email": "existing.user@example.com", "first_name": "Existing", "last_name": "User",
        "identity_type": "federatedID", "status": "active", "groups": {"AEM-PROD-AUTHORS"},
    }

    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    at.radio(key="navigation").set_value("User search").run(timeout=30)
    assert not at.exception

    [b for b in at.button if b.label == "Sync users from Adobe"][0].click().run(timeout=30)
    assert not at.exception
    # The sync handler calls st.rerun() right after st.success(), same as the
    # User groups page's sync — the toast is gone by the next run, so check the
    # actual cached-count metric instead of the ephemeral message.
    assert any(m.label == "Cached users" and m.value != "0" for m in at.metric)

    tabs_query = [w for w in at.text_input if w.label == "Search cached users"]
    assert tabs_query, "browse tab's search field should be present after sync"
    tabs_query[0].set_value("").run(timeout=30)
    assert not at.exception

    selector = [w for w in at.selectbox if w.label == "Pick a cached user"]
    assert selector, "at least the mock user should be browsable after sync"
