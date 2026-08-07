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
    """The reported bug: clicking "+ New template" appeared to do nothing."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)

    _goto(at, "Templates")
    # First template ever — form is visible because the template list is empty.
    [w for w in at.text_input if w.label == "Template name"][0].set_value("First").run(timeout=30)
    [w for w in at.multiselect if w.label == "Adobe custom user groups"][0].set_value(
        [[w for w in at.multiselect if w.label == "Adobe custom user groups"][0].options[0]]
    ).run(timeout=30)
    [b for b in at.button if b.label == "Save"][0].click().run(timeout=30)
    assert not at.exception

    # Now templates is non-empty. Clicking "+ New template" must still show the form.
    [b for b in at.button if b.label == "+ New template"][0].click().run(timeout=30)
    assert not at.exception
    assert [w for w in at.multiselect if w.label == "Adobe custom user groups"], (
        "New template form is not visible after clicking '+ New template' — "
        "the create panel isn't rendering."
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
    [b for b in at.button if b.key == f"template_view_{id_a}"][0].click().run(timeout=30)
    [b for b in at.button if b.label == "Edit"][0].click().run(timeout=30)
    group_widget = [w for w in at.multiselect if w.label == "Adobe custom user groups"][0]
    assert list(group_widget.value) == [group_a]

    # Switch straight to editing a different template — no Save/Cancel in between.
    [b for b in at.button if b.key == f"template_view_{id_b}"][0].click().run(timeout=30)
    [b for b in at.button if b.label == "Edit"][0].click().run(timeout=30)
    assert not at.exception
    group_widget = [w for w in at.multiselect if w.label == "Adobe custom user groups"][0]
    assert list(group_widget.value) == [group_b], (
        f"stale selection from the previous template leaked in: {group_widget.value}"
    )


def test_editing_a_template_does_not_leak_into_the_provision_wizard(temp_db):
    """The Templates page's "which template am I managing" pointer and the
    Provision wizard's "which template did I apply" pointer used to share one
    session key (`active_template_id`). Merely clicking Edit on the Templates
    page and abandoning it — no Save/Cancel — left that key set, so the wizard
    would later show a phantom "Template applied" banner for a template
    nobody actually applied there. They must be independent now."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)

    database.create_template_record("QA Template", "", "AEM", ["AEM-DEV-DEVELOPERS"], "actor@example.com")

    _goto(at, "Templates")
    [b for b in at.button if b.label == "View"][0].click().run(timeout=30)
    [b for b in at.button if b.label == "Edit"][0].click().run(timeout=30)
    assert at.session_state["template_mode"] == "Edit"

    # Abandon the edit — no Save/Cancel — and drive the wizard to the Access
    # step without ever touching a template selector there.
    _goto(at, "Provision access")
    at.text_area[0].set_value("someone@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Validate and continue"][0].click().run(timeout=30)
    [b for b in at.button if b.label == "Continue to access"][0].click().run(timeout=30)
    assert not at.exception
    assert at.session_state["active_template_id"] is None, "wizard's applied-template pointer must be untouched"
    assert not [i for i in at.info if "Template applied" in i.value], (
        "phantom 'Template applied' banner leaked in from browsing the Templates page"
    )


def test_applying_a_template_with_unsynced_groups_warns_instead_of_silently_dropping_them(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)

    template_id = database.create_template_record(
        "Stale Template", "", "AEM", ["AEM-RETIRED-1", "AEM-RETIRED-2"], "actor@example.com",
    )

    _goto(at, "Provision access")
    at.text_area[0].set_value("someone@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Validate and continue"][0].click().run(timeout=30)
    [b for b in at.button if b.label == "Continue to access"][0].click().run(timeout=30)
    [w for w in at.selectbox if w.key == "provision_template_id"][0].set_value(template_id).run(timeout=30)
    [b for b in at.button if b.label == "Apply template"][0].click().run(timeout=30)

    assert any("aren't in the synced group cache" in w.value for w in at.warning), (
        "expected a warning naming the missing groups instead of a silently empty selection"
    )
    assert [b for b in at.button if b.label == "Build preview"][0].disabled

    # "Remove template" must get you unstuck: clears the banner and lets you
    # pick groups manually without it reappearing.
    [b for b in at.button if b.label == "Remove template"][0].click().run(timeout=30)
    assert not at.exception
    assert at.session_state["active_template_id"] is None
    assert not [i for i in at.info if "Template applied" in i.value]
    options = [w for w in at.multiselect if w.label == "Adobe custom user groups"][0].options
    [w for w in at.multiselect if w.label == "Adobe custom user groups"][0].set_value([options[0]]).run(timeout=30)
    assert not [b for b in at.button if b.label == "Build preview"][0].disabled


def test_templates_page_only_defaults_to_the_create_form_when_none_exist(temp_db):
    """Landing straight on a blank Create form makes sense on a brand-new org
    with zero templates — it should NOT keep happening on every visit once
    templates exist, crowding out the neutral "pick one" state."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)

    _goto(at, "Templates")
    assert any(m.value.startswith("##### Create template") for m in at.markdown), (
        "expected the bootstrap case (no templates yet) to default into the Create form"
    )

    database.create_template_record("QA Template", "", "AEM", ["AEM-DEV-DEVELOPERS"], "actor@example.com")
    at2 = AppTest.from_file(APP_PATH)
    at2.run(timeout=30)
    _goto(at2, "Templates")
    assert not any(m.value.startswith("##### Create template") for m in at2.markdown), (
        "a fresh visit with existing templates should NOT auto-open a blank Create form"
    )
    assert any("Select a template on the left" in i.value for i in at2.info)


def test_dashboard_create_template_action_always_opens_the_create_form(temp_db):
    """Regression: once Templates stopped defaulting to the Create form when
    templates already exist, this Dashboard shortcut silently stopped doing
    what its label promises unless it explicitly requests Create mode."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)
    database.create_template_record("QA Template", "", "AEM", ["AEM-DEV-DEVELOPERS"], "actor@example.com")

    _goto(at, "Dashboard")
    [b for b in at.button if b.label == "Create template"][0].click().run(timeout=30)
    assert not at.exception
    assert any(m.value.startswith("##### Create template") for m in at.markdown)


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
