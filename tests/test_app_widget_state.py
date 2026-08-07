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
    at.text_area[0].set_value("someone.tester@example.com").run(timeout=30)
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
    at.text_area[0].set_value("someone.tester@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Validate and continue"][0].click().run(timeout=30)
    [b for b in at.button if b.label == "Continue to access"][0].click().run(timeout=30)
    [w for w in at.selectbox if w.key == "provision_template_id"][0].set_value(template_id).run(timeout=30)
    [b for b in at.button if b.label == "Apply template"][0].click().run(timeout=30)

    assert any("aren't in the synced group cache" in w.value for w in at.warning), (
        "expected a warning naming the missing groups instead of a silently empty selection"
    )
    assert [b for b in at.button if b.label == "Build preview"][0].disabled
    assert at.session_state["selected_groups"] == [], "no groups from this template were addable"

    # "Forget" must get you unstuck: clears the reference and lets you pick
    # groups manually without the warning reappearing.
    [b for b in at.button if b.label == "Forget"][0].click().run(timeout=30)
    assert not at.exception
    assert at.session_state["active_template_id"] is None
    assert not [c for c in at.caption if "Last template applied" in c.value]

    ms = [w for w in at.multiselect if w.label == "Adobe custom user groups"][0]
    ms.set_value([ms.options[0]]).run(timeout=30)
    [b for b in at.button if b.label == "Add selected groups"][0].click().run(timeout=30)
    assert not at.exception
    assert at.session_state["selected_groups"]
    assert not [b for b in at.button if b.label == "Build preview"][0].disabled


def test_template_favorite_and_custom_group_all_combine_into_one_selection(temp_db):
    """Reported bug: applying a template showed its groups in a table, but
    Build preview stayed disabled/stuck — because the custom group picker's
    multiselect was itself the single source of truth for selected_groups,
    overwritten on every render regardless of what Apply template or Add
    favorites had just set. Template, favorites, and the custom picker must
    all be pure *add* actions into one authoritative list, each provably
    surviving the others, with Build preview reflecting the union."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)  # AEM-DEV-DEVELOPERS, AEM-PROD-AUTHORS, AEP-DATA-ENGINEERS, CJA-ANALYSTS
    database.replace_favorite_groups("local.user@example.com", ["AEP-DATA-ENGINEERS"])
    template_id = database.create_template_record("test", "", "CJA", ["CJA-ANALYSTS"], "actor@example.com")

    _goto(at, "Provision access")
    at.text_area[0].set_value("someone.tester@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Validate and continue"][0].click().run(timeout=30)
    [b for b in at.button if b.label == "Continue to access"][0].click().run(timeout=30)

    # 1. Apply template — only this in play so far.
    [w for w in at.selectbox if w.key == "provision_template_id"][0].set_value(template_id).run(timeout=30)
    [b for b in at.button if b.label == "Apply template"][0].click().run(timeout=30)
    assert not at.exception
    assert at.session_state["selected_groups"] == ["CJA-ANALYSTS"]
    assert not [b for b in at.button if b.label == "Build preview"][0].disabled, (
        "template groups alone must already enable Build preview"
    )

    # 2. Add a favorite on top — must not lose the template's group.
    [w for w in at.multiselect if w.label == "Quick add favorites"][0].set_value(["AEP-DATA-ENGINEERS"]).run(timeout=30)
    [b for b in at.button if b.label == "Add selected favorites"][0].click().run(timeout=30)
    assert not at.exception
    assert set(at.session_state["selected_groups"]) == {"CJA-ANALYSTS", "AEP-DATA-ENGINEERS"}

    # 3. Add a custom group via search on top — must not lose the other two.
    ms = [w for w in at.multiselect if w.label == "Adobe custom user groups"][0]
    remaining_option = [o for o in ms.options if "AEM-PROD-AUTHORS" in o][0]
    ms.set_value([remaining_option]).run(timeout=30)
    [b for b in at.button if b.label == "Add selected groups"][0].click().run(timeout=30)
    assert not at.exception
    assert set(at.session_state["selected_groups"]) == {"CJA-ANALYSTS", "AEP-DATA-ENGINEERS", "AEM-PROD-AUTHORS"}
    assert not [b for b in at.button if b.label == "Build preview"][0].disabled

    # The "Selected groups" table (the actual source of truth) must list all
    # three, each with its own Remove button.
    for name in ["CJA-ANALYSTS", "AEP-DATA-ENGINEERS", "AEM-PROD-AUTHORS"]:
        assert [b for b in at.button if b.key == f"remove_selected_group_{name}"], (
            f"expected a Remove row for {name} in the Selected groups table"
        )

    # Build preview must actually succeed end-to-end from here.
    [b for b in at.button if b.label == "Build preview"][0].click().run(timeout=30)
    assert not at.exception
    assert at.session_state["provision_step"] == 4


def test_removing_a_selected_group_takes_it_out_of_the_selection(temp_db):
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)

    _goto(at, "Provision access")
    at.text_area[0].set_value("someone.tester@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Validate and continue"][0].click().run(timeout=30)
    [b for b in at.button if b.label == "Continue to access"][0].click().run(timeout=30)

    ms = [w for w in at.multiselect if w.label == "Adobe custom user groups"][0]
    ms.set_value([ms.options[0], ms.options[1]]).run(timeout=30)
    [b for b in at.button if b.label == "Add selected groups"][0].click().run(timeout=30)
    assert len(at.session_state["selected_groups"]) == 2

    to_remove = at.session_state["selected_groups"][0]
    [b for b in at.button if b.key == f"remove_selected_group_{to_remove}"][0].click().run(timeout=30)
    assert not at.exception
    assert to_remove not in at.session_state["selected_groups"]
    assert len(at.session_state["selected_groups"]) == 1


def test_add_selected_favorites_is_disabled_until_something_is_picked(temp_db):
    """Reported bug: clicking "Add selected favorites" with nothing picked in
    the "Quick add favorites" box was a silent no-op — no warning, nothing
    added, Build preview stayed disabled with zero feedback about why. Must
    disable the button instead, matching every other gated action in this app."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)
    database.replace_favorite_groups("local.user@example.com", ["AEM-PROD-AUTHORS"])

    _goto(at, "Provision access")
    at.text_area[0].set_value("someone.tester@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Validate and continue"][0].click().run(timeout=30)
    [b for b in at.button if b.label == "Continue to access"][0].click().run(timeout=30)

    add_btn = [b for b in at.button if b.label == "Add selected favorites"][0]
    assert add_btn.disabled, "must be disabled before anything is selected in the quick-add box"

    [w for w in at.multiselect if w.label == "Quick add favorites"][0].set_value(["AEM-PROD-AUTHORS"]).run(timeout=30)
    add_btn2 = [b for b in at.button if b.label == "Add selected favorites"][0]
    assert not add_btn2.disabled
    add_btn2.click().run(timeout=30)
    assert not at.exception
    assert at.session_state["selected_groups"] == ["AEM-PROD-AUTHORS"]
    assert not [b for b in at.button if b.label == "Build preview"][0].disabled


def test_a_favorite_saved_under_different_casing_still_appears(temp_db):
    """Same case-drift class as the template-casing bug above: list_favorite_groups()
    is stored free text, and Adobe isn't guaranteed to return identical casing for
    the same group across syncs — a favorite must not silently vanish from the
    quick-add list just because the catalog's casing for it changed since."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)  # caches "AEM-PROD-AUTHORS"
    database.replace_favorite_groups("local.user@example.com", ["aem-prod-authors"])  # different case

    _goto(at, "Provision access")
    at.text_area[0].set_value("someone.tester@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Validate and continue"][0].click().run(timeout=30)
    [b for b in at.button if b.label == "Continue to access"][0].click().run(timeout=30)

    quick_add = [w for w in at.multiselect if w.label == "Quick add favorites"]
    assert quick_add, "the casing mismatch must not make the favorite disappear from quick-add"
    # .options holds the format_func-rendered labels, not the raw values — the
    # "AEM" system suffix only resolves correctly if the label lookup matched
    # the catalog's canonical casing, not the favorite's stored casing.
    assert quick_add[0].options == ["AEM-PROD-AUTHORS · AEM"], "should resolve to the catalog's current canonical casing"


def test_applying_a_template_with_different_group_name_casing_still_selects_it(temp_db):
    """The actual root cause behind a real "Build preview stays disabled" report:
    a group can be genuinely present in the synced cache but under different
    casing than what got saved into the template — Adobe doesn't guarantee
    identical casing for the same group across syncs. This must resolve to the
    catalog's current casing, not get treated as missing (group_picker()'s own
    default-matching, and the stale-groups warning, were both exact-case)."""
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    _sync_groups(at)  # caches "AEM-PROD-AUTHORS" (see MockAdobeClient.groups)

    template_id = database.create_template_record(
        "Legacy Template", "", "AEM", ["aem-prod-authors"], "actor@example.com",  # different case
    )

    _goto(at, "Provision access")
    at.text_area[0].set_value("someone.tester@example.com").run(timeout=30)
    [b for b in at.button if b.label == "Validate and continue"][0].click().run(timeout=30)
    [b for b in at.button if b.label == "Continue to access"][0].click().run(timeout=30)
    [w for w in at.selectbox if w.key == "provision_template_id"][0].set_value(template_id).run(timeout=30)
    [b for b in at.button if b.label == "Apply template"][0].click().run(timeout=30)

    assert not at.warning, "a case-only difference must not be reported as a missing group"
    assert at.session_state["selected_groups"] == ["AEM-PROD-AUTHORS"], (
        "should resolve to the catalog's current canonical casing"
    )
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
