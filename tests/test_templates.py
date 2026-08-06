from __future__ import annotations

import sqlite3

import pytest

from adobe_access import database
from adobe_access.templates import (
    TemplateValidationError,
    create_template,
    delete_template,
    duplicate_template,
    get_template,
    list_templates,
    update_template,
)


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "templates.db")
    database.initialize()
    return database.DB_PATH


def test_template_crud_and_duplicate(temp_db):
    template_id = create_template(
        "CJA Analyst",
        "Standard reporting access",
        "CJA",
        ["BSC-CJA-REPORTING", "BSC-CJA-USERS", "BSC-CJA-USERS"],
        "tester@example.com",
    )
    template = get_template(template_id)
    assert template["name"] == "CJA Analyst"
    assert template["groups"] == ["BSC-CJA-REPORTING", "BSC-CJA-USERS"]
    assert template["group_count"] == 2

    update_template(
        template_id,
        "CJA Analyst Updated",
        "Updated",
        "CJA",
        ["BSC-CJA-REPORTING"],
        "editor@example.com",
    )
    updated = get_template(template_id)
    assert updated["name"] == "CJA Analyst Updated"
    assert updated["groups"] == ["BSC-CJA-REPORTING"]

    duplicate_id = duplicate_template(template_id, "CJA Analyst Copy", "tester@example.com")
    duplicated = get_template(duplicate_id)
    assert duplicated["groups"] == updated["groups"]
    assert len(list_templates()) == 2

    delete_template(duplicate_id)
    assert get_template(duplicate_id) is None
    assert len(list_templates()) == 1


def test_template_validation_and_unique_name(temp_db):
    with pytest.raises(TemplateValidationError):
        create_template("", "", "AEM", ["AEM-USERS"], "tester")
    with pytest.raises(TemplateValidationError):
        create_template("AEM Author", "", "AEM", [], "tester")

    create_template("AEM Author", "", "AEM", ["AEM-USERS"], "tester")
    with pytest.raises(ValueError, match="already exists"):
        create_template("aem author", "", "AEM", ["AEM-OTHER"], "tester")


def test_legacy_template_migration(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE access_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            groups_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "INSERT INTO access_templates(name,description,groups_json,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("Legacy", "Old format", '["GROUP-A", "GROUP-B"]', "2026-01-01", "2026-01-01"),
        )
        conn.commit()

    database.initialize()
    templates = list_templates()
    assert templates.iloc[0]["name"] == "Legacy"
    assert templates.iloc[0]["groups"] == ["GROUP-A", "GROUP-B"]
