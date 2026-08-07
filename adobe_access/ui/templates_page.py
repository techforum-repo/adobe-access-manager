from __future__ import annotations

import pandas as pd
import streamlit as st

from adobe_access.database import record
from adobe_access.templates import (
    TemplateValidationError,
    create_template,
    delete_template,
    duplicate_template,
    get_template,
    list_templates,
    update_template,
)
from adobe_access.ui.shared import group_catalog, group_picker, reset_group_picker

# `template_manage_id` is this page's OWN "which template am I looking at" pointer —
# deliberately a different session key from `active_template_id`, which is the
# Provision wizard's "which template did I apply to the current request" pointer.
# They used to share one key: clicking Edit/Duplicate/Delete here (without ever
# saving or cancelling) left a stale id behind that the wizard would later pick up
# and render as a phantom "Template applied" banner for a template nobody applied.
# Keep them separate.


def render() -> None:
    groups = group_catalog()
    templates = list_templates()
    st.subheader("Access templates")
    st.caption("Create reusable bundles of Adobe custom user groups for the Provision Access workflow.")

    left, right = st.columns([2, 3], gap="large")

    with left:
        _render_list(templates)

    with right:
        _render_manage_panel(templates, groups)


def _render_list(templates: pd.DataFrame) -> None:
    if st.button("+ New template", type="primary", width='stretch'):
        st.session_state.template_mode = "Create"
        st.session_state.template_manage_id = None
        reset_group_picker("template_form")
        st.rerun()

    query = st.text_input("Search templates", key="template_search")
    systems = ["All"] + sorted(templates["system"].dropna().astype(str).unique().tolist()) if not templates.empty else ["All"]
    system_filter = st.selectbox("System", systems, key="template_system_filter")

    filtered = templates.copy()
    if not filtered.empty:
        if query:
            mask = (
                filtered["name"].str.contains(query, case=False, na=False)
                | filtered["description"].str.contains(query, case=False, na=False)
            )
            filtered = filtered[mask]
        if system_filter != "All":
            filtered = filtered[filtered["system"] == system_filter]

    st.divider()
    if filtered.empty:
        st.info("No templates match the current filters." if not templates.empty else "No templates yet — create one above.")
        return

    for _, row in filtered.iterrows():
        template_id = int(row["id"])
        is_active = st.session_state.template_manage_id == template_id and st.session_state.template_mode != "Create"
        with st.container(border=True):
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"{'**→** ' if is_active else ''}**{row['name']}**")
            c1.caption(f"{row['system']} · {row['group_count']} group(s) · updated {str(row['updated_at'])[:10]}")
            if c2.button("View", key=f"template_view_{template_id}", width='stretch', disabled=is_active):
                st.session_state.template_manage_id = template_id
                st.session_state.template_mode = "Browse"
                st.rerun()


def _render_manage_panel(templates: pd.DataFrame, groups: pd.DataFrame) -> None:
    mode = st.session_state.template_mode
    manage_id = st.session_state.template_manage_id
    active = get_template(int(manage_id)) if manage_id else None

    if mode != "Create" and not active:
        # Any mode but Create needs a template to act on — falls back here if
        # it vanished (e.g. deleted in another tab) or nothing was ever picked.
        mode = st.session_state.template_mode = "Browse"
        st.session_state.template_manage_id = None

    if mode == "Create":
        _render_form("Create", None, groups)
    elif mode == "Edit" and active:
        _render_form("Edit", active, groups)
    elif mode == "Duplicate" and active:
        _render_duplicate(active)
    elif mode == "Delete" and active:
        _render_delete(active)
    elif mode == "Browse" and active:
        _render_detail(active, groups)
    elif templates.empty:
        # Nothing exists yet to browse — bootstrap straight into the create
        # form instead of an empty-state message with nothing to click.
        _render_form("Create", None, groups)
    else:
        st.info("Select a template on the left to view it, or create a new one.")


def _render_detail(active: dict, groups: pd.DataFrame) -> None:
    with st.container(border=True):
        st.markdown(f"### {active['name']}")
        st.caption(f"System: {active['system']} · {active['group_count']} group(s) · updated {str(active['updated_at'])[:10]}")
        st.write(active.get("description") or "No description")

        # Case-insensitive, matching group_picker()'s own default-resolution —
        # Adobe isn't guaranteed to return identical casing for the same group
        # across syncs, so an exact-case comparison here would flag a
        # perfectly valid, currently-cached group as "missing" just because
        # its casing drifted since the template was saved.
        group_lookup = {str(row["adobe_group_name"]).strip().casefold(): row for _, row in groups.iterrows()} if not groups.empty else {}
        missing = [g for g in active["groups"] if str(g).strip().casefold() not in group_lookup]
        if missing:
            st.warning(
                f"{len(missing)} of {active['group_count']} group(s) aren't in the synced group cache — they'll "
                f"be silently skipped if this template is applied in Provision access: {', '.join(missing)}. "
                "They may have been renamed or removed in Adobe; try re-syncing on User groups, or edit this template."
            )
        if active["groups"]:
            rows = [{
                "Display name": group_lookup.get(str(g).strip().casefold(), {}).get("display_name") or g,
                "System": group_lookup.get(str(g).strip().casefold(), {}).get("system") or active.get("system") or "Other",
                "Adobe user group": g,
                "In synced cache": "No" if g in missing else "Yes",
            } for g in active["groups"]]
            st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
        else:
            st.caption("This template does not contain any groups.")

        e1, e2, e3 = st.columns(3)
        if e1.button("Edit", width='stretch'):
            st.session_state.template_mode = "Edit"
            reset_group_picker("template_form")
            st.rerun()
        if e2.button("Duplicate", width='stretch'):
            st.session_state.template_mode = "Duplicate"
            st.rerun()
        if e3.button("Delete", width='stretch'):
            st.session_state.template_mode = "Delete"
            st.rerun()


def _render_form(mode: str, active: dict | None, groups: pd.DataFrame) -> None:
    manage_id = st.session_state.template_manage_id
    title = "Create template" if mode == "Create" else f"Edit template: {active['name']}"
    with st.container(border=True):
        st.markdown(f"##### {title}")
        default_name = active["name"] if active else ""
        default_description = active["description"] if active else ""
        default_system = active["system"] if active else "Other"
        default_groups = active["groups"] if active else []

        form_name = st.text_input("Template name", value=default_name, key=f"template_name_{mode}_{manage_id}")
        form_description = st.text_area("Description", value=default_description, key=f"template_description_{mode}_{manage_id}")
        catalog_systems = sorted(set(groups["system"].dropna().astype(str).tolist()) | {default_system, "Other"}) if not groups.empty else [default_system, "Other"]
        form_system = st.selectbox(
            "System",
            catalog_systems,
            index=catalog_systems.index(default_system) if default_system in catalog_systems else 0,
            key=f"template_system_{mode}_{manage_id}",
        )
        selected_groups = group_picker(groups, "template_form", default_groups)
        save_col, cancel_col = st.columns([1, 4])
        if save_col.button("Save", type="primary", width='stretch'):
            try:
                if mode == "Create":
                    new_id = create_template(form_name, form_description, form_system, selected_groups, st.session_state.actor)
                    action = "template-create"
                else:
                    update_template(int(active["id"]), form_name, form_description, form_system, selected_groups, st.session_state.actor)
                    new_id = int(active["id"])
                    action = "template-update"
                record(st.session_state.actor, action, "", selected_groups, "Success", form_name.strip())
                st.session_state.template_mode = "Browse"
                st.session_state.template_manage_id = new_id
                reset_group_picker("template_form")
                st.toast("Template saved.")
                st.rerun()
            except (TemplateValidationError, ValueError) as exc:
                st.error(str(exc))
        if cancel_col.button("Cancel", width='content'):
            # Editing an existing template goes back to viewing it; cancelling a
            # brand-new one goes back to the neutral "nothing selected" prompt
            # rather than reopening the same blank form.
            st.session_state.template_mode = "Browse"
            reset_group_picker("template_form")
            st.rerun()


def _render_duplicate(active: dict) -> None:
    with st.container(border=True):
        st.markdown(f"##### Duplicate template: {active['name']}")
        # Keyed per-template (like the Create/Edit name field above) so switching
        # which template you're duplicating — without navigating away — doesn't
        # leave a stale suggested name from whichever template you duplicated first.
        duplicate_name = st.text_input(
            "New template name", value=f"{active['name']} Copy",
            key=f"duplicate_template_name_{active['id']}",
        )
        st.caption(f"The duplicate will contain the same {active['group_count']} groups.")
        d1, d2 = st.columns([1, 4])
        if d1.button("Create duplicate", type="primary"):
            try:
                new_id = duplicate_template(int(active["id"]), duplicate_name, st.session_state.actor)
                record(st.session_state.actor, "template-duplicate", "", active["groups"], "Success", duplicate_name.strip())
                st.session_state.template_mode = "Browse"
                st.session_state.template_manage_id = new_id
                st.toast("Template duplicated.")
                st.rerun()
            except (TemplateValidationError, ValueError) as exc:
                st.error(str(exc))
        if d2.button("Cancel duplicate"):
            st.session_state.template_mode = "Browse"
            st.rerun()


def _render_delete(active: dict) -> None:
    with st.container(border=True):
        st.markdown(f"##### Delete template: {active['name']}")
        st.warning("Deleting a template cannot be undone. Adobe users and groups will not be changed.")
        # Keyed per-template so switching which template is staged for deletion
        # (without navigating away) doesn't show a stale confirmation typed for
        # a different template. The exact-name match below already prevents any
        # unsafe deletion from stale text — this is purely a UX fix.
        confirmation = st.text_input(f"Type {active['name']} to confirm", key=f"delete_template_confirmation_{active['id']}")
        x1, x2 = st.columns([1, 4])
        if x1.button("Delete permanently", type="primary", disabled=confirmation != active["name"]):
            delete_template(int(active["id"]))
            record(st.session_state.actor, "template-delete", "", active["groups"], "Success", active["name"])
            st.session_state.template_mode = "Create"
            st.session_state.template_manage_id = None
            st.toast("Template deleted.")
            st.rerun()
        if x2.button("Cancel delete"):
            st.session_state.template_mode = "Browse"
            st.rerun()
