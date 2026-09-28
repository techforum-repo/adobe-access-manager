from __future__ import annotations

"""Saved project names: the projects table, the "Lastname(ProjectName)" parsing
helpers, filtering the local user cache by project, and the UI wiring in
Provision access / User search / Settings."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from adobe_access import database, provisioning
from adobe_access.client import client
from adobe_access.users import browse_cached_users, known_projects
from adobe_access.utils import split_project_suffix, with_project_suffix

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "projects.db")
    database.initialize()
    return database.DB_PATH


@pytest.fixture(autouse=True)
def _seed_mock_users():
    provisioning.client.users.clear()
    provisioning.client.users["jane.doe@example.com"] = {
        "email": "jane.doe@example.com", "first_name": "Jane", "last_name": "Doe(Apollo)",
        "identity_type": "federatedID", "status": "active", "groups": set(),
    }
    yield
    provisioning.client.users.clear()


def _goto(at: AppTest, page: str) -> None:
    at.radio(key="navigation").set_value(page).run(timeout=30)
    assert not at.exception, (page, list(at.exception))


def _cache_users() -> None:
    database.replace_managed_users([
        {"email": "jane.doe@example.com", "first_name": "Jane", "last_name": "Doe(Apollo)", "groups": set()},
        {"email": "john.roe@example.com", "first_name": "John", "last_name": "Roe(apollo)", "groups": set()},
        {"email": "ann.lee@example.com", "first_name": "Ann", "last_name": "Lee(Gemini)", "groups": set()},
        {"email": "bob.kay@example.com", "first_name": "Bob", "last_name": "Kay", "groups": set()},
    ])


@pytest.mark.parametrize(("value", "expected"), [
    ("Doe(Apollo)", ("Doe", "Apollo")),
    ("Doe (Apollo) ", ("Doe", "Apollo")),
    ("Van Der Berg(Project X)", ("Van Der Berg", "Project X")),
    ("Doe", ("Doe", "")),
    ("", ("", "")),
])
def test_split_project_suffix(value, expected):
    assert split_project_suffix(value) == expected


def test_with_project_suffix_round_trips():
    assert with_project_suffix("Doe", "Apollo") == "Doe(Apollo)"
    assert with_project_suffix("Doe", "") == "Doe"
    assert split_project_suffix(with_project_suffix("Doe", "Apollo")) == ("Doe", "Apollo")


def test_add_list_delete_projects(temp_db):
    assert database.add_project("Zeta", "a@example.com") is True
    assert database.add_project("apollo", "a@example.com") is True
    assert database.add_project("APOLLO", "a@example.com") is False  # case-insensitive duplicate
    assert database.list_projects() == ["apollo", "Zeta"]
    database.delete_project("Zeta")
    assert database.list_projects() == ["apollo"]


@pytest.mark.parametrize("name", ["", "   ", "Bad(Name)"])
def test_add_project_rejects_invalid_names(temp_db, name):
    with pytest.raises(ValueError):
        database.add_project(name, "a@example.com")


def test_browse_cached_users_filters_by_project_case_insensitively(temp_db):
    _cache_users()
    result = browse_cached_users(project="Apollo")
    assert sorted(result["email"]) == ["jane.doe@example.com", "john.roe@example.com"]
    assert browse_cached_users(project="gemini")["email"].tolist() == ["ann.lee@example.com"]
    assert len(browse_cached_users()) == 4
    assert browse_cached_users("ann", project="Apollo").empty


def test_known_projects_merges_saved_and_cached(temp_db):
    database.add_project("Mercury", "a@example.com")
    _cache_users()
    assert known_projects() == ["Apollo", "Gemini", "Mercury"]


def test_browse_tab_project_filter(temp_db):
    _cache_users()
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "User search")
    at.selectbox(key="user_browse_project").set_value("Gemini").run(timeout=30)
    assert not at.exception
    assert any("1 cached user(s)" in c.value for c in at.caption)


def test_edit_name_changes_project_suffix(temp_db):
    database.add_project("Gemini", "a@example.com")
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "User search")
    [w for w in at.text_area if w.label == "User email(s)"][0].set_value("jane.doe@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Search Adobe"][0].click().run(timeout=30)
    assert not at.exception

    # The edit form shows the base last name and the current project separately.
    assert [w for w in at.text_input if w.label == "Last name"][0].value == "Doe"
    project = [w for w in at.selectbox if w.label == "Project"][0]
    assert project.value == "Apollo"

    project.set_value("Gemini").run(timeout=30)
    [b for b in at.button if b.label == "Save"][0].click().run(timeout=30)
    assert not at.exception
    assert client.users["jane.doe@example.com"]["last_name"] == "Doe(Gemini)"


def test_provision_wizard_saves_a_new_project_and_applies_it(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "Provision access")
    at.selectbox(key="project_name_input").set_value("+ New project…").run(timeout=30)
    at.text_input(key="project_name_new").set_value("Hermes").run(timeout=30)
    at.text_area[0].set_value("someone.tester@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Validate and continue"][0].click().run(timeout=30)
    assert not at.exception
    assert database.list_projects() == ["Hermes"]
    assert at.session_state["users"].iloc[0]["last_name"] == "Tester(Hermes)"


def test_settings_page_adds_and_deletes_projects(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "Settings")
    [w for w in at.text_input if w.placeholder == "New project name"][0].set_value("Orion")
    [b for b in at.button if b.label == "Add project"][0].click().run(timeout=30)
    assert not at.exception
    assert database.list_projects() == ["Orion"]

    at.button(key="delete_project_Orion").click().run(timeout=30)
    assert not at.exception
    assert database.list_projects() == []
