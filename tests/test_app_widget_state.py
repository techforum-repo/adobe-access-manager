from __future__ import annotations

"""Regression tests for a class of Streamlit bug: a keyed widget ignores its
`default=`/`value=` once the key already exists in session_state. Several
pages set up a *new* set of defaults (a different template, a different Copy
Access source user, a different reused request) while staying on the same
page — without an explicit reset, the widget silently keeps showing
whatever was selected/typed for the *previous* thing instead.

These exercise the real app end-to-end via Streamlit's AppTest, because the
bug lives in widget/session_state interaction that pure unit tests of
adobe_access.* can't see.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from adobe_access import database

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "app_widget_state.db")
    database.initialize()
    return database.DB_PATH


def _goto(at: AppTest, page: str) -> None:
    at.radio(key="navigation").set_value(page).run(timeout=30)
    assert not at.exception, (page, list(at.exception))


def _sync_groups(at: AppTest) -> None:
    _goto(at, "User groups")
    [b for b in at.button if b.label == "Sync from Adobe"][0].click().run(timeout=30)
    assert not at.exception


def test_new_template_form_is_visible_even_when_templates_already_exist(temp_db):
    """The reported bug: clicking "New template" appeared to do nothing."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)

    _goto(at, "Templates")
    group = [w for w in at.multiselect if w.label == "Adobe custom user groups"]
    # First template ever — form is visible because the template list is empty.
    [w for w in at.text_input if w.label == "Template name"][0].set_value("First").run(timeout=30)
    [w for w in at.multiselect if w.label == "Adobe custom user groups"][0].set_value(
        [[w for w in at.multiselect if w.label == "Adobe custom user groups"][0].options[0]]
    ).run(timeout=30)
    [b for b in at.button if b.label == "Save"][0].click().run(timeout=30)
    assert not at.exception

    # Now templates is non-empty. Clicking "New template" must still show the form.
    [b for b in at.button if b.label == "New template"][0].click().run(timeout=30)
    assert not at.exception
    assert [w for w in at.multiselect if w.label == "Adobe custom user groups"], (
        "New template form is not visible after clicking 'New template' — "
        "the create expander is collapsed."
    )


def test_switching_edit_target_does_not_leak_previous_templates_groups(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)

    groups_df = database.read_managed_groups()
    group_a = groups_df["adobe_group_name"].iloc[0]
    group_b = groups_df["adobe_group_name"].iloc[1]
    id_a = database.create_template_record("Alpha", "", "Other", [group_a], "actor@example.com")
    id_b = database.create_template_record("Beta", "", "Other", [group_b], "actor@example.com")

    _goto(at, "Templates")
    selector = [w for w in at.selectbox if w.label == "Select a template to view or manage"][0]
    selector.set_value(id_a).run(timeout=30)
    [b for b in at.button if b.label == "Edit"][0].click().run(timeout=30)
    group_widget = [w for w in at.multiselect if w.label == "Adobe custom user groups"][0]
    assert list(group_widget.value) == [group_a]

    # Switch straight to editing a different template — no Save/Cancel in between.
    selector = [w for w in at.selectbox if w.label == "Select a template to view or manage"][0]
    selector.set_value(id_b).run(timeout=30)
    [b for b in at.button if b.label == "Edit"][0].click().run(timeout=30)
    assert not at.exception
    group_widget = [w for w in at.multiselect if w.label == "Adobe custom user groups"][0]
    assert list(group_widget.value) == [group_b], (
        f"stale selection from the previous template leaked in: {group_widget.value}"
    )


def test_loading_a_different_copy_access_source_resets_the_group_selection(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)

    from adobe_access.client import client as mock_client
    mock_client.users["source.a@example.com"] = {
        "email": "source.a@example.com", "first_name": "Source", "last_name": "A",
        "identity_type": "federatedID", "status": "active", "groups": {"AEM-PROD-AUTHORS"},
    }
    mock_client.users["source.b@example.com"] = {
        "email": "source.b@example.com", "first_name": "Source", "last_name": "B",
        "identity_type": "federatedID", "status": "active", "groups": {"CJA-ANALYSTS", "AEP-DATA-ENGINEERS"},
    }

    _goto(at, "Copy access")
    [w for w in at.text_input if w.label == "Source user email"][0].set_value("source.a@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Load source user"][0].click().run(timeout=30)

    # Load a different source, same page, no navigation away.
    [w for w in at.text_input if w.label == "Source user email"][0].set_value("source.b@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Load source user"][0].click().run(timeout=30)
    assert not at.exception

    group_widget = [w for w in at.multiselect if w.label == "Groups to copy"][0]
    assert set(group_widget.value) == {"CJA-ANALYSTS", "AEP-DATA-ENGINEERS"}, (
        f"expected all of source B's groups pre-selected, got {group_widget.value}"
    )
