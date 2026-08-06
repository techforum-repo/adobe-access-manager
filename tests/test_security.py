from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from adobe_access import database
from adobe_access.utils import harden_file_permissions, sanitize_log_field

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def test_sanitize_log_field_neutralizes_line_injection():
    forged = "actor@example.com\nfake=line\r\ninjected=here"
    result = sanitize_log_field(forged)
    assert "\n" not in result
    assert "\r" not in result
    # The original text is still visible/auditable, just not exploitable as
    # separate log lines.
    assert "actor@example.com" in result
    assert "fake=line" in result


def test_sanitize_log_field_strips_other_control_characters():
    assert sanitize_log_field("a\x00b\x1fc\x7fd") == "a b c d"


def test_sanitize_log_field_leaves_ordinary_text_alone():
    assert sanitize_log_field("Applied template: CJA Analyst") == "Applied template: CJA Analyst"


@pytest.mark.skipif(os.name == "nt", reason="chmod-based hardening is POSIX-only")
def test_harden_file_permissions_restricts_a_file(tmp_path):
    target = tmp_path / "secret.db"
    target.write_text("data")
    target.chmod(0o644)
    harden_file_permissions(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="chmod-based hardening is POSIX-only")
def test_harden_file_permissions_restricts_a_directory_with_execute_bit(tmp_path):
    target = tmp_path / "logs"
    target.mkdir()
    target.chmod(0o755)
    harden_file_permissions(target, mode=0o700)
    assert stat.S_IMODE(target.stat().st_mode) == 0o700


def test_harden_file_permissions_never_raises_on_a_missing_path(tmp_path):
    harden_file_permissions(tmp_path / "does-not-exist.db")  # must not raise


@pytest.mark.skipif(os.name == "nt", reason="chmod-based hardening is POSIX-only")
def test_initialize_hardens_the_database_file_permissions(tmp_path, monkeypatch):
    db_path = tmp_path / "hardened.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.initialize()
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600


def test_dashboard_does_not_use_unsafe_html_for_user_controlled_template_names():
    """Regression guard for the stored-XSS fix: template names are free text
    from the Templates page and must never be interpolated into markdown
    rendered with unsafe_allow_html=True."""
    source = (Path(__file__).resolve().parent.parent / "adobe_access" / "ui" / "dashboard.py").read_text()
    for line in source.splitlines():
        if "unsafe_allow_html=True" in line:
            assert "label" not in line, f"user-controlled value interpolated into unsafe HTML: {line.strip()}"


def test_dashboard_renders_a_request_with_a_dangerous_template_name(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "xss.db")
    database.initialize()
    dangerous_name = "<img src=x onerror=alert(1)>"
    database.save_recent_request(
        "actor@example.com", [{"email": "a@example.com"}], ["G1"], "Preview", {},
        template_id=None, template_name=dangerous_name,
    )

    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    at.radio(key="navigation").set_value("Dashboard").run(timeout=30)
    assert not at.exception

    # Streamlit's default st.markdown() (no unsafe_allow_html) escapes the value
    # at render time rather than executing it — the raw source text still shows
    # up in the element tree (that part is expected/harmless), it's just never
    # passed through as live HTML.
    values = [m.value for m in at.markdown]
    assert any(dangerous_name in v for v in values)
