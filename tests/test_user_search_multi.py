from __future__ import annotations

"""UI-wiring coverage for User search's multi-email lookup and pagination —
searching more than one email at once switches from the single-result detail
view straight to a paginated results table."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from adobe_access import database, provisioning

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "user_search_multi.db")
    database.initialize()
    return database.DB_PATH


@pytest.fixture(autouse=True)
def _seed_mock_users():
    provisioning.client.users.clear()
    for index in range(1, 4):
        email = f"person{index}.user@example.com"
        provisioning.client.users[email] = {
            "email": email, "first_name": f"Person{index}", "last_name": "User",
            "identity_type": "federatedID", "status": "active", "groups": {"AEM-PROD-AUTHORS"},
        }
    yield
    provisioning.client.users.clear()


def _goto(at: AppTest, page: str) -> None:
    at.radio(key="navigation").set_value(page).run(timeout=30)
    assert not at.exception, (page, list(at.exception))


def test_multiple_emails_show_a_paginated_summary_instead_of_one_detail_view(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "User search")
    emails = "\n".join([
        "person1.user@example.com",
        "person2.user@example.com",
        "missing.user@example.com",
    ])
    [w for w in at.text_area if w.label == "User email(s)"][0].set_value(emails).run(timeout=30)
    [b for b in at.button if b.label == "Search Adobe"][0].click().run(timeout=30)
    assert not at.exception

    metrics = {m.label: m.value for m in at.metric if m.label in ("Searched", "Found", "Not found", "Errors")}
    assert metrics == {"Searched": "3", "Found": "2", "Not found": "1", "Errors": "0"}
    assert any("Page 1 of 1" in m.value for m in at.markdown)


def test_pagination_controls_step_through_pages(temp_db):
    # 11 users at the default page size (10) forces a second page, without
    # needing a page size smaller than the smallest real option.
    for index in range(4, 12):
        email = f"person{index}.user@example.com"
        provisioning.client.users[email] = {
            "email": email, "first_name": f"Person{index}", "last_name": "User",
            "identity_type": "federatedID", "status": "active", "groups": set(),
        }
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "User search")
    emails = "\n".join(f"person{i}.user@example.com" for i in range(1, 12))
    [w for w in at.text_area if w.label == "User email(s)"][0].set_value(emails).run(timeout=30)
    [b for b in at.button if b.label == "Search Adobe"][0].click().run(timeout=30)
    assert not at.exception
    assert any("Page 1 of 2" in m.value for m in at.markdown)

    [b for b in at.button if "Next" in b.label][0].click().run(timeout=30)
    assert any("Page 2 of 2" in m.value for m in at.markdown)

    [b for b in at.button if "Previous" in b.label][0].click().run(timeout=30)
    assert any("Page 1 of 2" in m.value for m in at.markdown)


def test_single_email_still_shows_the_direct_detail_view(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "User search")
    [w for w in at.text_area if w.label == "User email(s)"][0].set_value("person1.user@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Search Adobe"][0].click().run(timeout=30)
    assert not at.exception
    assert not any(m.label == "Searched" for m in at.metric)
    assert any("Person1 User" in m.value for m in at.markdown)


def test_all_not_found_can_be_prepared_for_provisioning(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "User search")
    emails = "\n".join(["new1.person@example.com", "new2.person@example.com"])
    [w for w in at.text_area if w.label == "User email(s)"][0].set_value(emails).run(timeout=30)
    [b for b in at.button if b.label == "Search Adobe"][0].click().run(timeout=30)
    assert not at.exception

    [b for b in at.button if "not-found user(s) as a new provisioning request" in b.label][0].click().run(timeout=30)
    assert not at.exception
    assert at.session_state["provision_step"] == 2
    assert sorted(at.session_state["users"]["email"].tolist()) == ["new1.person@example.com", "new2.person@example.com"]
