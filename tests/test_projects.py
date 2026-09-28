from __future__ import annotations

"""Saved project names: the projects table, the "Lastname(ProjectName)" parsing
helpers, filtering the local user cache by project, and the UI wiring in
Provision access / User search / Settings."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from adobe_access import database, provisioning
from adobe_access.client import client
from adobe_access.config import settings
from adobe_access.users import browse_cached_users
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


def test_browse_cached_users_filters_by_saved_project_suffix_case_insensitively(temp_db):
    database.add_project("Apollo", "a@example.com")
    database.add_project("Gemini", "a@example.com")
    _cache_users()
    result = browse_cached_users(project="apollo")
    assert sorted(result["email"]) == ["jane.doe@example.com", "john.roe@example.com"]
    assert set(result["project"]) == {"Apollo"}  # saved spelling, not the suffix's
    assert browse_cached_users(project="gemini")["email"].tolist() == ["ann.lee@example.com"]
    assert len(browse_cached_users()) == 4
    assert browse_cached_users("ann", project="Apollo").empty


def test_unsaved_last_name_suffix_is_not_a_project(temp_db):
    _cache_users()  # suffixes present, but nothing saved in Settings
    assert set(browse_cached_users()["project"]) == {""}
    assert browse_cached_users(project="Apollo").empty


def test_local_association_overrides_suffix_and_tags_existing_users(temp_db):
    database.add_project("Gemini", "a@example.com")
    database.add_project("Apollo", "a@example.com")
    _cache_users()
    database.set_user_project("BOB.KAY@example.com", "gemini", "a@example.com")  # no suffix at all
    database.set_user_project("jane.doe@example.com", "Gemini", "a@example.com")  # suffix says Apollo
    assert sorted(browse_cached_users(project="Gemini")["email"]) == [
        "ann.lee@example.com", "bob.kay@example.com", "jane.doe@example.com",
    ]
    assert browse_cached_users(project="Apollo")["email"].tolist() == ["john.roe@example.com"]

    database.set_user_project("bob.kay@example.com", "", "a@example.com")
    assert database.get_user_project("bob.kay@example.com") == ""


def test_delete_project_removes_its_local_links(temp_db):
    database.add_project("Gemini", "a@example.com")
    database.set_user_project("bob.kay@example.com", "Gemini", "a@example.com")
    assert database.project_user_counts() == {"gemini": 1}
    database.delete_project("Gemini")
    assert database.user_project_map() == {}


def test_browse_tab_project_filter_offers_only_saved_projects(temp_db):
    database.add_project("Gemini", "a@example.com")
    _cache_users()  # also carries an unsaved "(Apollo)" suffix
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "User search")
    project = at.selectbox(key="user_browse_project")
    assert project.options == ["All projects", "Gemini"]
    project.set_value("Gemini").run(timeout=30)
    assert not at.exception
    assert any("1 cached user(s)" in c.value for c in at.caption)


def test_detail_view_links_an_existing_user_to_a_project_locally(temp_db):
    database.add_project("Gemini", "a@example.com")
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "User search")
    [w for w in at.text_area if w.label == "User email(s)"][0].set_value("jane.doe@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Search Adobe"][0].click().run(timeout=30)
    assert not at.exception

    project = [w for w in at.selectbox if w.label == "Project"][0]
    assert project.options == ["(none)", "Gemini"]
    project.set_value("Gemini").run(timeout=30)
    [b for b in at.button if b.label == "Save project"][0].click().run(timeout=30)
    assert not at.exception

    assert database.get_user_project("jane.doe@example.com") == "Gemini"
    assert client.users["jane.doe@example.com"]["last_name"] == "Doe(Apollo)", "Adobe must not be touched"


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


def test_execute_links_new_and_existing_users_to_the_batch_project(temp_db, monkeypatch):
    monkeypatch.setattr(settings, "adobe_write_enabled", True)
    database.add_project("Gemini", "a@example.com")
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto(at, "User groups")
    [b for b in at.button if b.label == "Sync from Adobe"][0].click().run(timeout=30)

    _goto(at, "Provision access")
    at.selectbox(key="project_name_input").set_value("Gemini").run(timeout=30)
    at.text_area[0].set_value("jane.doe@example.com\nnew.person@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Validate and continue"][0].click().run(timeout=30)
    [b for b in at.button if b.label == "Continue to access"][0].click().run(timeout=30)
    ms = [w for w in at.multiselect if w.label == "Adobe custom user groups"][0]
    ms.set_value([ms.options[0]]).run(timeout=30)
    [b for b in at.button if b.label == "Add selected groups"][0].click().run(timeout=30)
    [b for b in at.button if b.label == "Build preview"][0].click().run(timeout=30)
    assert not at.exception
    at.checkbox(key="execute_confirm").check().run(timeout=30)
    [b for b in at.button if b.label.startswith("⚠️ Execute")][0].click().run(timeout=30)
    assert not at.exception

    assert database.get_user_project("jane.doe@example.com") == "Gemini"  # existing user
    assert database.get_user_project("new.person@example.com") == "Gemini"  # new user
    assert client.users["new.person@example.com"]["last_name"] == "Person(Gemini)"
    assert client.users["jane.doe@example.com"]["last_name"] == "Doe(Apollo)"  # existing name untouched
