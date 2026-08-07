from __future__ import annotations

"""Settings page UI — previously had no test coverage at all."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from adobe_access import database

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "settings_page.db")
    database.initialize()
    return database.DB_PATH


def _goto_settings(at: AppTest) -> None:
    at.radio(key="navigation").set_value("Settings").run(timeout=30)
    assert not at.exception


def test_saving_a_valid_country_code_persists_it(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto_settings(at)

    [w for w in at.text_input if w.label == "Default country"][0].set_value("ca").run(timeout=30)
    [b for b in at.button if b.label == "Save settings"][0].click().run(timeout=30)
    assert not at.exception
    assert not at.error

    _goto_settings(at)
    assert [w for w in at.text_input if w.label == "Default country"][0].value == "CA"


@pytest.mark.parametrize("bad_value", ["United States", "U", "USA", "12"])
def test_an_invalid_country_code_is_rejected_and_not_saved(temp_db, bad_value):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _goto_settings(at)

    [w for w in at.text_input if w.label == "Default country"][0].set_value(bad_value).run(timeout=30)
    [b for b in at.button if b.label == "Save settings"][0].click().run(timeout=30)
    assert not at.exception
    assert any("two-letter country code" in e.value for e in at.error)
    assert "default_country" not in database.get_setting_overrides()
