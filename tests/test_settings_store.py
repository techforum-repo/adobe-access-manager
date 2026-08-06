from __future__ import annotations

import pytest

from adobe_access import database, settings_store


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "settings.db")
    database.initialize()
    return database.DB_PATH


def test_defaults_come_from_env_backed_settings(temp_db):
    values = settings_store.current_values()
    assert values["allowed_email_domains"] == "example.com"
    assert values["default_country"] == "US"
    assert values["default_identity_type"] == "federatedID"
    assert values["auto_adobe_validation"] is True
    assert settings_store.overridden_keys() == set()


def test_save_overrides_effective_values(temp_db):
    settings_store.save(
        {
            "allowed_email_domains": "example.com, example.com",
            "default_country": "CA",
            "cache_ttl_seconds": 120,
            "auto_adobe_validation": False,
        },
        actor="tester@example.com",
    )
    assert settings_store.allowed_domains() == {"example.com", "example.com"}
    assert settings_store.default_country() == "CA"
    assert settings_store.cache_ttl_seconds() == 120
    assert settings_store.auto_adobe_validation() is False
    assert "default_country" in settings_store.overridden_keys()


def test_reset_clears_overrides_back_to_env_defaults(temp_db):
    settings_store.save({"default_country": "CA"}, actor="tester@example.com")
    assert settings_store.default_country() == "CA"
    settings_store.reset()
    assert settings_store.default_country() == "US"
    assert settings_store.overridden_keys() == set()


def test_unparseable_override_falls_back_to_default(temp_db):
    database.set_setting_overrides({"cache_ttl_seconds": "not-a-number"}, actor="tester@example.com")
    assert settings_store.cache_ttl_seconds() == 600
