from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from adobe_access import settings_store
from adobe_access.client import client
from adobe_access.config import settings
from adobe_access.database import (
    list_favorite_groups,
    record,
    save_execution,
    save_recent_request,
    update_request_status,
)
from adobe_access.provisioning import (
    build_user_table,
    execute,
    execution_summary,
    extract_emails_from_first_column,
    preview,
    preview_summary,
    run,
    validate_users_against_adobe,
)
from adobe_access.templates import get_template, list_templates
from adobe_access.ui.shared import group_catalog, group_picker, reset_group_picker, reset_provisioning
from adobe_access.utils import safe_csv


def render() -> None:
    steps = st.columns(4)
    for index, title in enumerate(["1. Users", "2. Validate", "3. Access", "4. Review"], start=1):
        marker = "✓" if st.session_state.provision_step > index else ("●" if st.session_state.provision_step == index else "○")
        steps[index - 1].markdown(f"<div class='step'>{marker} {title}</div>", unsafe_allow_html=True)
    st.write("")

    if st.session_state.provision_step == 1:
        _render_step_users()
    elif st.session_state.provision_step == 2:
        _render_step_validate()
    elif st.session_state.provision_step == 3:
        _render_step_access()
    else:
        _render_step_review()


def _render_step_users() -> None:
    st.caption("Emails must match the firstname.lastname@domain naming convention (a trailing digit like john2.doe is OK) — anything else is flagged Invalid on the next step.")
    source = st.radio("Input method", ["Paste emails", "Upload CSV/XLSX"], horizontal=True)
    emails: list[str] = []
    if source == "Paste emails":
        text = st.text_area("One email per line, or separated by comma/semicolon", height=160)
        emails = [item.strip() for item in text.replace(",", "\n").replace(";", "\n").splitlines() if item.strip()]
    else:
        upload = st.file_uploader("Upload a file with emails in the first column (no header row needed)", type=["csv", "xlsx"])
        if upload:
            try:
                uploaded_df = (
                    pd.read_csv(upload, header=None) if Path(upload.name).suffix.lower() == ".csv"
                    else pd.read_excel(upload, header=None)
                )
            except Exception as exc:
                st.error(f"Could not read this file: {exc}")
                uploaded_df = pd.DataFrame()
            emails = extract_emails_from_first_column(uploaded_df)
            if not emails:
                st.warning("No values were found in the first column of this file.")
    if st.button("Validate and continue", type="primary", disabled=not emails):
        st.session_state.users = build_user_table(emails)
        st.session_state.validation_checked = False
        st.session_state.provision_step = 2
        st.rerun()


def _render_step_validate() -> None:
    auto_validate = settings_store.auto_adobe_validation()
    st.subheader("Validate users")
    st.caption(
        "Local validation is shown immediately. Valid users are checked against Adobe "
        + ("automatically when this step opens." if auto_validate else "when you click \"Check Adobe now\" below (auto-validation is off in Settings).")
    )

    valid_before_lookup = st.session_state.users[st.session_state.users["validation"] == "Valid"]
    if auto_validate and not st.session_state.validation_checked and not valid_before_lookup.empty:
        with st.spinner("Automatically checking users in Adobe..."):
            st.session_state.users = validate_users_against_adobe(st.session_state.users)
        st.session_state.validation_checked = True
    elif not auto_validate and not st.session_state.validation_checked and not valid_before_lookup.empty:
        if st.button("Check Adobe now", type="primary"):
            with st.spinner("Checking users in Adobe..."):
                st.session_state.users = validate_users_against_adobe(st.session_state.users)
            st.session_state.validation_checked = True
            st.rerun()

    disabled_columns = ["validation", "notes"]
    if "adobe_status" in st.session_state.users.columns:
        disabled_columns += ["adobe_status", "current_group_count", "lookup_details"]
    st.session_state.users = st.data_editor(
        st.session_state.users,
        width='stretch',
        hide_index=True,
        disabled=disabled_columns,
    )
    invalid = st.session_state.users[st.session_state.users["validation"] != "Valid"]
    valid = st.session_state.users[st.session_state.users["validation"] == "Valid"]
    duplicate_count = int((st.session_state.users["validation"] == "Duplicate").sum())
    invalid_count = int((st.session_state.users["validation"] == "Invalid").sum())
    v1, v2, v3 = st.columns(3)
    v1.metric("Valid rows", len(valid))
    v2.metric("Duplicates", duplicate_count)
    v3.metric("Invalid", invalid_count)
    if not invalid.empty:
        st.download_button("Download excluded rows", safe_csv(invalid), "excluded-users.csv", "text/csv")
    if st.session_state.validation_checked and not valid.empty:
        if st.button("Refresh Adobe validation", help="Run the Adobe lookup again after editing user details or after a temporary lookup failure."):
            with st.spinner("Refreshing Adobe user validation..."):
                st.session_state.users = validate_users_against_adobe(st.session_state.users)
            st.rerun()
    if st.session_state.validation_checked and "adobe_status" in st.session_state.users.columns:
        statuses = st.session_state.users["adobe_status"].value_counts().to_dict()
        st.success(
            f"Adobe validation completed: {statuses.get('Existing', 0)} existing, "
            f"{statuses.get('New', 0)} new, {statuses.get('Lookup failed', 0)} lookup failures."
        )
    b1, b2 = st.columns([1, 4])
    if b1.button("Back"):
        st.session_state.provision_step = 1
        st.rerun()
    if b2.button("Continue to access", type="primary", disabled=not bool(st.session_state.users["include"].any())):
        st.session_state.provision_step = 3
        st.rerun()


def _render_step_access() -> None:
    """Template, favorites, and the custom search picker are all pure *add*
    actions into `st.session_state.selected_groups` — none of them mirror or
    get overwritten by another widget's current display state. The "Selected
    groups" table below is the single source of truth Build preview reads,
    built directly from that list every render, not from what any picker
    widget currently happens to show. (Previously the custom picker's
    multiselect was itself the source of truth — `st.session_state.selected_groups
    = group_picker(...)` on every render — so if that widget's own displayed
    state ever fell out of sync with what Apply template/Add favorites had
    just set, its return value would silently overwrite the correct selection
    with a stale one. Reported as "applying a template shows it in a table
    but Build preview doesn't work.")
    """
    groups = group_catalog()
    templates = list_templates()
    catalog_lookup = {
        str(row["adobe_group_name"]).strip().casefold(): row for _, row in groups.iterrows()
    } if not groups.empty else {}

    st.markdown("###### 1. Apply a template")
    if not templates.empty:
        template_options = {int(row["id"]): str(row["name"]) for _, row in templates.iterrows()}
        selected_template_id = st.selectbox(
            "Access template",
            options=[None] + list(template_options),
            format_func=lambda value: "No template" if value is None else template_options[value],
            key="provision_template_id",
        )
        if selected_template_id is not None:
            template = get_template(int(selected_template_id))
            st.caption(
                f"{template['system']} · {template['group_count']} groups"
                + (f" · {template['description']}" if template.get('description') else "")
            )
            if st.button("Apply template", type="secondary"):
                template_groups = list(template["groups"])
                # Case-insensitive, resolved to the catalog's current canonical
                # casing (not the template's possibly-stale casing) — Adobe
                # isn't guaranteed to return identical casing for the same
                # group across syncs, and what ends up in selected_groups is
                # sent to Adobe verbatim later, so using a stale case here
                # isn't just a display nit.
                missing: list[str] = []
                added: list[str] = []
                for g in template_groups:
                    key = str(g).strip().casefold()
                    if key in catalog_lookup:
                        added.append(catalog_lookup[key]["adobe_group_name"])
                    else:
                        missing.append(g)
                st.session_state.selected_groups = list(dict.fromkeys(st.session_state.selected_groups + added))
                st.session_state.active_template_id = int(selected_template_id)
                st.session_state["_template_apply_missing"] = missing
                st.toast(f"Applied template: {template['name']} — added {len(added)} group(s).")
                st.rerun()
    else:
        st.caption("No access templates are available. Create one from Templates.")
    if st.session_state.active_template_id:
        active_template = get_template(int(st.session_state.active_template_id))
        if active_template:
            r1, r2 = st.columns([5, 1])
            r1.caption(f"Last template applied: {active_template['name']}")
            if r2.button("Forget", key="remove_active_template", help="Only clears this reference — doesn't remove any groups it already added."):
                st.session_state.active_template_id = None
                st.session_state.pop("provision_template_id", None)
                st.rerun()
    # Set for exactly one rerun by the "Apply template" click above — shown
    # once, right after the apply that produced it, not on every later render.
    missing_from_apply = st.session_state.pop("_template_apply_missing", None)
    if missing_from_apply:
        st.warning(
            f"{len(missing_from_apply)} group(s) from that template aren't in the synced group cache, so "
            f"they weren't added: {', '.join(missing_from_apply)}. They may have been renamed or removed "
            "in Adobe — try re-syncing on User groups, or edit the template."
        )

    st.markdown("###### 2. Add favorites")
    # Case-insensitive, matching the template-apply resolution above — a
    # favorite saved under one casing shouldn't vanish from this list just
    # because the catalog's casing for that group drifted on a later sync.
    favorites = [
        catalog_lookup[key]["adobe_group_name"] for g in list_favorite_groups(st.session_state.actor)
        if (key := str(g).strip().casefold()) in catalog_lookup
    ]
    if not favorites:
        st.caption("No favorite groups yet — pin some from User groups.")
    else:
        favorite_rows = groups[groups["adobe_group_name"].isin(favorites)]
        favorite_labels = {row["adobe_group_name"]: f"{row['display_name']} · {row['system']}" for _, row in favorite_rows.iterrows()}
        add_favorites = st.multiselect(
            "Quick add favorites",
            favorites,
            format_func=lambda value: favorite_labels.get(value, value),
            key="provision_favorite_quick_add",
        )
        if st.button("Add selected favorites", disabled=not add_favorites):
            st.session_state.selected_groups = list(dict.fromkeys(st.session_state.selected_groups + add_favorites))
            st.session_state.pop("provision_favorite_quick_add", None)
            st.toast(f"Added {len(add_favorites)} favorite group(s).")
            st.rerun()

    st.markdown("###### 3. Search and add custom groups")
    # Always starts empty — a pure picker for *new* additions, not a mirror of
    # the current selection, so it never has stale state to fall out of sync
    # with (the previous source of the reported bug).
    candidate_groups = group_picker(groups, "provision", [])
    if st.button("Add selected groups", disabled=not candidate_groups):
        st.session_state.selected_groups = list(dict.fromkeys(st.session_state.selected_groups + candidate_groups))
        reset_group_picker("provision")
        st.toast(f"Added {len(candidate_groups)} group(s).")
        st.rerun()

    st.divider()
    st.markdown("###### Selected groups")
    if not st.session_state.selected_groups:
        st.info("Nothing selected yet — apply a template, add favorites, or search and add groups above.")
    else:
        # A plain st.dataframe/data_editor can't offer a per-row remove action
        # (data_editor's own row-deletion is a checkbox/trash-icon grid gesture
        # with no way to test it — Streamlit's AppTest only exposes .value on
        # a rendered dataframe, not simulated row edits) — render each row
        # with its own button instead, same pattern as Templates page's list.
        privileged_names = []
        header = st.columns([3, 2, 3, 1])
        header[0].markdown("**Display name**")
        header[1].markdown("**System**")
        header[2].markdown("**Adobe user group**")
        for name in st.session_state.selected_groups:
            meta = catalog_lookup.get(str(name).strip().casefold(), {})
            display_name = (meta.get("display_name") if hasattr(meta, "get") else None) or name
            system = (meta.get("system") if hasattr(meta, "get") else None) or "Other"
            is_privileged = bool(meta.get("privileged", False)) if hasattr(meta, "get") else False
            if is_privileged:
                privileged_names.append(display_name)
            c1, c2, c3, c4 = st.columns([3, 2, 3, 1])
            c1.write(f"{display_name}{' ⚠️' if is_privileged else ''}")
            c2.write(system)
            c3.write(name)
            if c4.button("Remove", key=f"remove_selected_group_{name}"):
                st.session_state.selected_groups = [g for g in st.session_state.selected_groups if g != name]
                st.rerun()
        if privileged_names:
            st.warning("Privileged groups selected: " + ", ".join(privileged_names))

    st.divider()
    b1, b2 = st.columns([1, 4])
    if b1.button("Back"):
        st.session_state.provision_step = 2
        st.rerun()
    if b2.button("Build preview", type="primary", disabled=not st.session_state.selected_groups):
        with st.spinner("Checking users and current memberships in Adobe..."):
            st.session_state.preview = preview(st.session_state.users, st.session_state.selected_groups)
        summary = preview_summary(st.session_state.preview)
        template = get_template(int(st.session_state.active_template_id)) if st.session_state.active_template_id else None
        request_users = st.session_state.users.to_dict("records")
        st.session_state.last_request_id = save_recent_request(
            st.session_state.actor,
            request_users,
            st.session_state.selected_groups,
            "Preview",
            summary,
            int(st.session_state.active_template_id) if st.session_state.active_template_id else None,
            template.get("name", "") if template else "",
        )
        record(st.session_state.actor, "provision-preview", "", st.session_state.selected_groups, "Success", str(summary))
        st.session_state.provision_step = 4
        st.rerun()


def _render_step_review() -> None:
    summary = preview_summary(st.session_state.preview)
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Users", summary["users"])
    m2.metric("Existing", summary["existing"])
    m3.metric("New", summary["new"])
    m4.metric("Assignments to add", summary["assignments"])
    m5.metric("Already assigned", summary["already"])
    failed = summary["failures"]
    if len(st.session_state.preview) <= 12:
        for _, user_row in st.session_state.preview.iterrows():
            with st.container(border=True):
                c1, c2 = st.columns([2, 3])
                c1.markdown(f"**{user_row.get('name') or user_row['email']}**")
                c1.caption(user_row["email"])
                c1.write(user_row["user_action"])
                c2.markdown(f"**Will add:** {user_row['groups_to_add']}")
                c2.caption(f"Already assigned: {user_row['already_assigned']}")
                if user_row.get("lookup") != "OK":
                    c2.error(str(user_row.get("lookup")))
    else:
        st.dataframe(st.session_state.preview, width='stretch', hide_index=True)
    st.download_button("Download preview", safe_csv(st.session_state.preview), "provision-preview.csv", "text/csv")
    confirm = st.checkbox("I reviewed the users and selected groups")
    c1, c2, c3 = st.columns([1, 2, 3])
    if c1.button("Back"):
        st.session_state.provision_step = 3
        st.rerun()
    if c2.button("Start over"):
        reset_provisioning()
        st.rerun()
    if c3.button("Run test", type="primary", disabled=not confirm or failed > 0,
                  help="Sends Adobe's action request with testOnly=true — Adobe validates the payload but makes no changes."):
        output = []
        for _, row in st.session_state.users[st.session_state.users["include"] == True].iterrows():  # noqa: E712
            email = str(row["email"])
            try:
                result = run(client.provision(email, str(row["first_name"]), str(row["last_name"]), st.session_state.selected_groups, test_only=True))
                status = "Test passed" if result["success"] else "Failed"
                detail = f"Would create: {result['created']}; Groups: {', '.join(result['groups_added']) or 'None'}"
                record(st.session_state.actor, "provision-test", email, st.session_state.selected_groups, status, detail)
                output.append({"email": email, "status": status, "would_create": result["created"], "groups_to_add": "; ".join(result["groups_added"]), "details": detail})
            except Exception as exc:
                record(st.session_state.actor, "provision-test", email, st.session_state.selected_groups, "Failed", str(exc))
                output.append({"email": email, "status": "Failed", "would_create": False, "groups_to_add": "", "details": str(exc)})
        result_df = pd.DataFrame(output)
        st.dataframe(result_df, width='stretch', hide_index=True)
        st.download_button("Download results", safe_csv(result_df), "provision-test-results.csv", "text/csv")

    st.divider()
    st.markdown("##### Execute")
    if not settings.adobe_write_enabled:
        st.info(
            "Execute is disabled. Set ADOBE_WRITE_ENABLED=true in .env and restart the app to enable live "
            "writes — only after Run test payloads look correct. This can never be turned on from the UI."
        )
    else:
        to_create = int((~st.session_state.preview.get("exists", pd.Series(dtype=bool)).fillna(False)).sum())
        st.warning(
            f"⚠️ Live write mode is enabled. You are about to:\n\n"
            f"- **Create {to_create} user(s)**\n"
            f"- **Add {summary['assignments']} group assignment(s)**\n\n"
            f"This makes real changes in Adobe. Running the same request again is safe — "
            f"only missing changes are applied."
        )
        execute_confirm = st.checkbox(
            "I confirm this will make real changes in Adobe and I have reviewed the preview above.",
            key="execute_confirm",
        )
        if st.button(
            "⚠️ Execute (live Adobe changes)", type="primary",
            disabled=not execute_confirm or failed > 0,
        ):
            started_at = datetime.now(timezone.utc).isoformat()
            with st.spinner("Executing — this makes real changes in Adobe..."):
                results = execute(st.session_state.users, st.session_state.selected_groups, test_only=False)
            completed_at = datetime.now(timezone.utc).isoformat()
            exec_summary = execution_summary(results)
            execution_id = save_execution(
                st.session_state.last_request_id, st.session_state.actor,
                started_at, completed_at, test_only=False,
                results=results.to_dict("records"),
            )
            if st.session_state.last_request_id:
                new_status = "Executed" if exec_summary["failed"] == 0 else (
                    "Execution failed" if exec_summary["failed"] == len(results) else "Partially executed"
                )
                update_request_status(st.session_state.last_request_id, new_status)
            for _, row in results.iterrows():
                detail = (
                    f"Created: {row['created']}; Groups added: {', '.join(row['groups_added']) or 'None'}; "
                    f"Retries: {row['retries']}" if row["success"] else str(row["error"])
                )
                record(
                    st.session_state.actor, "provision-execute", str(row["email"]),
                    st.session_state.selected_groups, "Success" if row["success"] else "Failed", detail,
                )
            st.success(f"Execution #{execution_id} complete.")
            e1, e2, e3, e4, e5, e6 = st.columns(6)
            e1.metric("Created", exec_summary["created"])
            e2.metric("Existing", exec_summary["existing"])
            e3.metric("Groups added", exec_summary["groups_added"])
            e4.metric("Already assigned", exec_summary["already_assigned"])
            e5.metric("Failed", exec_summary["failed"])
            e6.metric("Retries", exec_summary["retries"])
            display_results = results.drop(columns=["adobe_response"], errors="ignore").copy()
            display_results["groups_added"] = display_results["groups_added"].apply(lambda v: "; ".join(v) or "None")
            display_results["already_assigned"] = display_results["already_assigned"].apply(lambda v: "; ".join(v) or "None")
            st.dataframe(display_results, width='stretch', hide_index=True)
            with st.expander("Adobe response detail (per user)"):
                st.json(results[["email", "adobe_response"]].to_dict("records") if "adobe_response" in results.columns else [])
            dl1, dl2 = st.columns(2)
            dl1.download_button("Download execution CSV", safe_csv(display_results), f"execution-{execution_id}.csv", "text/csv")
            dl2.download_button("Download execution JSON", results.to_json(orient="records", indent=2), f"execution-{execution_id}.json", "application/json")
