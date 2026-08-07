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


def test_current_values_is_cached_until_save_or_reset_invalidates_it(temp_db, monkeypatch):
    calls = {"n": 0}
    real_get_overrides = database.get_setting_overrides

    def counting_get_overrides():
        calls["n"] += 1
        return real_get_overrides()

    monkeypatch.setattr(database, "get_setting_overrides", counting_get_overrides)

    settings_store.current_values()
    settings_store.current_values()
    settings_store.current_values()
    assert calls["n"] == 1, "repeated reads within one cache lifetime should hit the DB once"

    settings_store.save({"default_country": "CA"}, actor="tester@example.com")
    settings_store.current_values()
    assert calls["n"] == 2, "save() must invalidate the cache so the next read is fresh"

    settings_store.reset()
    settings_store.current_values()
    assert calls["n"] == 3, "reset() must invalidate the cache so the next read is fresh"


def test_settings_store_cache_does_not_leak_across_a_different_db_path(tmp_path, monkeypatch):
    """Guards the exact hazard tests/conftest.py's autouse fixture exists for:
    without resetting the module-level cache, a value read against one DB_PATH
    could leak into a test that swaps DB_PATH to a different file."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "first.db")
    database.initialize()
    settings_store.save({"default_country": "CA"}, actor="tester@example.com")
    assert settings_store.default_country() == "CA"

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "second.db")
    database.initialize()
    settings_store._invalidate_cache()  # what the autouse fixture does between real tests
    assert settings_store.default_country() == "US", (
        "stale cache from the first DB_PATH leaked into the second"
    )
