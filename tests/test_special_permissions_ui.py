from __future__ import annotations

"""UI-wiring coverage for the "Special permissions" section on User search
and Compare users — the underlying logic (special_permissions(),
compare_special_permissions(), membership_table()'s exclusion) is unit-tested
in test_user_search_actions.py; this confirms it's actually wired into the
real page, not just correct in isolation."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from adobe_access import database, provisioning

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "special_permissions_ui.db")
    database.initialize()
    return database.DB_PATH


@pytest.fixture(autouse=True)
def _seed_mock_users():
    provisioning.client.users.clear()
    provisioning.client.users["admin.user@example.com"] = {
        "email": "admin.user@example.com", "first_name": "Admin", "last_name": "User",
        "identity_type": "federatedID", "status": "active",
        "groups": {"AEM-PROD-AUTHORS", "_org_admin", "_product_admin_target", "_product_admin_aem"},
    }
    provisioning.client.users["plain.user@example.com"] = {
        "email": "plain.user@example.com", "first_name": "Plain", "last_name": "User",
        "identity_type": "federatedID", "status": "active",
        "groups": {"AEM-PROD-AUTHORS"},
    }
    yield
    provisioning.client.users.clear()


def _goto(at: AppTest, page: str) -> None:
    at.radio(key="navigation").set_value(page).run(timeout=30)
    assert not at.exception, (page, list(at.exception))


def test_user_search_shows_special_permissions_for_an_admin(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "User search")
    [w for w in at.text_input if w.label == "User email"][0].set_value("admin.user@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Search Adobe"][0].click().run(timeout=30)
    assert not at.exception

    assert any("Special permissions" in m.value for m in at.markdown)
    # System Administrator (single, no detail) renders as a flat flag line.
    assert any("System Administrator" in m.value for m in at.markdown)
    # Product Administrator (two entries) renders as a counted expander,
    # each product listed as its own line.
    expanders = [e for e in at.expander if e.label.startswith("Product Administrator")]
    assert expanders, "expected a Product Administrator expander"
    assert "(2)" in expanders[0].label
    assert any("target" in m.value for m in at.markdown)
    assert any("aem" in m.value for m in at.markdown)


def test_user_search_shows_no_special_permissions_section_for_a_plain_user(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "User search")
    [w for w in at.text_input if w.label == "User email"][0].set_value("plain.user@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Search Adobe"][0].click().run(timeout=30)
    assert not at.exception

    metric = [m for m in at.metric if m.label == "Special permissions"][0]
    assert metric.value == "0"
    assert not any("⚠️ Special permissions" in m.value for m in at.markdown)


def test_compare_users_shows_special_permissions_comparison(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "Compare users")
    [w for w in at.text_input if w.label == "First user email"][0].set_value("admin.user@example.com").run(timeout=30)
    [w for w in at.text_input if w.label == "Second user email"][0].set_value("plain.user@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Compare users"][0].click().run(timeout=30)
    assert not at.exception

    # Each user's own "Administrative rights" card (System Administrator flag
    # line, Product Administrator expander) renders once per side.
    assert any("System Administrator" in m.value for m in at.markdown)
    expanders = [e for e in at.expander if e.label.startswith("Product Administrator")]
    assert expanders and "(2)" in expanders[0].label

    # Plus the side-by-side diff table.
    assert any("Special permissions" in m.value for m in at.markdown)
    tables = [df.value for df in at.dataframe if "Role" in getattr(df.value, "columns", [])]
    assert tables, "expected a Special permissions comparison table"
    row = tables[0][tables[0]["Role"] == "System Administrator"].iloc[0]
    assert row["Result"] == "Only first user"
