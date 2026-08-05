from __future__ import annotations

from pathlib import Path

from adobe_access import database


def test_favorites_and_recent_requests(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.initialize()
    database.replace_favorite_groups("admin@bsci.com", ["Group B", "Group A", "Group A"])
    assert database.list_favorite_groups("admin@bsci.com") == ["Group A", "Group B"]

    request_id = database.save_recent_request(
        "admin@bsci.com",
        [{"email": "user@bsci.com", "include": True}],
        ["Group A"],
        "Preview",
        {"assignments": 1},
    )
    request = database.get_recent_request(request_id)
    assert request is not None
    assert request["groups"] == ["Group A"]
    assert request["summary"]["assignments"] == 1

    recent = database.list_recent_requests()
    assert recent.loc[0, "user_count"] == 1
    assert recent.loc[0, "summary_assignments"] == 1
