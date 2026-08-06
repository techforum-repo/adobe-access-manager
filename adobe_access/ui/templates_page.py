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


def render() -> None:
    groups = group_catalog()
    templates = list_templates()
    st.subheader("Access templates")
    st.caption("Create reusable bundles of Adobe custom user groups for the Provision Access workflow.")

    top1, top2, top3 = st.columns([3, 2, 1])
    template_query = top1.text_input("Search templates")
    systems = ["All"] + sorted(templates["system"].dropna().astype(str).unique().tolist()) if not templates.empty else ["All"]
    template_system = top2.selectbox("System", systems)
    if top3.button("New template", type="primary", width='stretch'):
        st.session_state.template_mode = "Create"
        st.session_state.active_template_id = None
        reset_group_picker("template_form")
        st.rerun()

    filtered_templates = templates.copy()
    if not filtered_templates.empty:
        if template_query:
            mask = (
                filtered_templates["name"].str.contains(template_query, case=False, na=False)
                | filtered_templates["description"].str.contains(template_query, case=False, na=False)
            )
            filtered_templates = filtered_templates[mask]
        if template_system != "All":
            filtered_templates = filtered_templates[filtered_templates["system"] == template_system]

    if filtered_templates.empty:
        st.info("No templates match the current filters." if not templates.empty else "No templates have been created.")
    else:
        table = filtered_templates[["name", "system", "description", "group_count", "updated_at"]].rename(
            columns={"name": "Template", "system": "System", "description": "Description", "group_count": "Groups", "updated_at": "Updated"}
        )
        st.dataframe(table, width='stretch', hide_index=True)

        selected_id = st.selectbox(
            "Select a template to view or manage",
            filtered_templates["id"].astype(int).tolist(),
            format_func=lambda value: str(filtered_templates.loc[filtered_templates["id"] == value, "name"].iloc[0]),
            key="template_selected_id",
        )
        selected_template = get_template(int(selected_id))
        with st.expander("Template groups", expanded=True):
            st.write(selected_template.get("description") or "No description")
            st.caption(f"System: {selected_template['system']} · {selected_template['group_count']} groups")
            st.dataframe(pd.DataFrame({"Adobe user group": selected_template["groups"]}), width='stretch', hide_index=True)

        a1, a2, a3 = st.columns(3)
        if a1.button("Edit", width='stretch'):
            st.session_state.template_mode = "Edit"
            st.session_state.active_template_id = int(selected_id)
            reset_group_picker("template_form")
            st.rerun()
        if a2.button("Duplicate", width='stretch'):
            st.session_state.template_mode = "Duplicate"
            st.session_state.active_template_id = int(selected_id)
            st.rerun()
        if a3.button("Delete", width='stretch'):
            st.session_state.template_mode = "Delete"
            st.session_state.active_template_id = int(selected_id)
            st.rerun()

    mode = st.session_state.template_mode
    active = get_template(int(st.session_state.active_template_id)) if st.session_state.active_template_id else None

    if mode in {"Create", "Edit"}:
        title = "Create template" if mode == "Create" else f"Edit template: {active['name']}"
        # Always expanded: this block only renders when the user just clicked
        # "New template" or "Edit", so the form must be visible immediately —
        # a collapsed expander here reads as the button having done nothing.
        with st.expander(title, expanded=True):
            default_name = active["name"] if active else ""
            default_description = active["description"] if active else ""
            default_system = active["system"] if active else "Other"
            default_groups = active["groups"] if active else []

            form_name = st.text_input("Template name", value=default_name, key=f"template_name_{mode}_{st.session_state.active_template_id}")
            form_description = st.text_area("Description", value=default_description, key=f"template_description_{mode}_{st.session_state.active_template_id}")
            catalog_systems = sorted(set(groups["system"].dropna().astype(str).tolist()) | {default_system, "Other"}) if not groups.empty else [default_system, "Other"]
            form_system = st.selectbox(
                "System",
                catalog_systems,
                index=catalog_systems.index(default_system) if default_system in catalog_systems else 0,
                key=f"template_system_{mode}_{st.session_state.active_template_id}",
            )
            selected_groups = group_picker(groups, "template_form", default_groups)
            save_col, cancel_col = st.columns([1, 4])
            if save_col.button("Save", type="primary", width='stretch'):
                try:
                    if mode == "Create":
                        create_template(form_name, form_description, form_system, selected_groups, st.session_state.actor)
                        action = "template-create"
                    else:
                        update_template(int(active["id"]), form_name, form_description, form_system, selected_groups, st.session_state.actor)
                        action = "template-update"
                    record(st.session_state.actor, action, "", selected_groups, "Success", form_name.strip())
                    st.session_state.template_mode = "Create"
                    st.session_state.active_template_id = None
                    reset_group_picker("template_form")
                    st.toast("Template saved.")
                    st.rerun()
                except (TemplateValidationError, ValueError) as exc:
                    st.error(str(exc))
            if cancel_col.button("Cancel", width='content'):
                st.session_state.template_mode = "Create"
                st.session_state.active_template_id = None
                reset_group_picker("template_form")
                st.rerun()

    elif mode == "Duplicate" and active:
        with st.expander(f"Duplicate template: {active['name']}", expanded=True):
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
                    duplicate_template(int(active["id"]), duplicate_name, st.session_state.actor)
                    record(st.session_state.actor, "template-duplicate", "", active["groups"], "Success", duplicate_name.strip())
                    st.session_state.template_mode = "Create"
                    st.session_state.active_template_id = None
                    st.toast("Template duplicated.")
                    st.rerun()
                except (TemplateValidationError, ValueError) as exc:
                    st.error(str(exc))
            if d2.button("Cancel duplicate"):
                st.session_state.template_mode = "Create"
                st.session_state.active_template_id = None
                st.rerun()

    elif mode == "Delete" and active:
        with st.expander(f"Delete template: {active['name']}", expanded=True):
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
                st.session_state.active_template_id = None
                st.toast("Template deleted.")
                st.rerun()
            if x2.button("Cancel delete"):
                st.session_state.template_mode = "Create"
                st.session_state.active_template_id = None
                st.rerun()
