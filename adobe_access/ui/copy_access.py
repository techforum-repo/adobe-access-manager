from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from adobe_access.client import client
from adobe_access.config import settings
from adobe_access.database import record, save_execution, save_recent_request, update_request_status
from adobe_access.provisioning import execute, execution_summary, run
from adobe_access.ui.shared import render_friendly_error
from adobe_access.utils import derive_name, safe_csv
from adobe_access.users import (
    UserLookupError,
    build_copy_access_preview,
    lookup_user,
    membership_table,
    normalize_lookup_email,
)


def render() -> None:
    st.subheader("Copy access")
    st.caption(
        "Copy synchronized Adobe custom user groups from one source user to one or more target "
        "users. Build a preview first — nothing changes in Adobe until you explicitly run a test "
        "or execute."
    )

    source_email = st.text_input("Source user email", value=st.session_state.get("copy_source_email", ""), key="copy_source_email")
    load_source = st.button("Load source user", type="primary", disabled="@" not in source_email)
    load_source = load_source or st.session_state.pop("_retry_copy_source", False)
    if load_source:
        try:
            source = lookup_user(source_email)
            if source is None:
                st.session_state.copy_source = None
                st.error("The source user was not found in Adobe.")
            else:
                st.session_state.copy_source = source
                _reset_preview_state()
                # "Groups to copy" is keyed and defaults to "all of them" — without
                # clearing it, loading a different source user on the same page
                # leaves it stuck on the previous source's (now invalid) selection,
                # silently landing on an empty selection instead of the new
                # source's full group list.
                st.session_state.pop("copy_selected_groups", None)
        except UserLookupError as exc:
            if render_friendly_error(exc, key="retry_copy_source", context=f"While loading source user {source_email.strip() or ''}."):
                st.session_state["_retry_copy_source"] = True
                st.rerun()

    source = st.session_state.get("copy_source")
    if not source:
        return

    source_memberships = membership_table(source)
    header_col, reset_col = st.columns([5, 1])
    header_col.success(f"Loaded {source.get('display_name') or source.get('email')} with {len(source_memberships)} synchronized custom user groups.")
    if reset_col.button("Start over"):
        st.session_state.copy_source = None
        st.session_state.copy_target_text = ""
        _reset_preview_state()
        st.session_state.pop("copy_selected_groups", None)
        st.rerun()
    if source_memberships.empty:
        st.info("The source user has no memberships in the locally synchronized custom user-group cache.")
        return

    source_options = source_memberships["adobe_group_name"].tolist()
    labels = {row["adobe_group_name"]: f"{row['display_name']} · {row['system']}" for _, row in source_memberships.iterrows()}
    selected_source_groups = st.multiselect(
        "Groups to copy",
        source_options,
        default=source_options,
        format_func=lambda name: labels.get(name, name),
        key="copy_selected_groups",
    )
    with st.expander("Source custom user groups", expanded=False):
        st.dataframe(
            source_memberships[["display_name", "system", "adobe_group_name"]].rename(columns={
                "display_name": "Display name", "system": "System", "adobe_group_name": "Adobe group"
            }),
            width='stretch', hide_index=True,
        )

    target_text = st.text_area(
        "Target users",
        value=st.session_state.get("copy_target_text", ""),
        placeholder="firstname.lastname@example.com\nsecond.user@example.com",
        help="Enter one or more email addresses separated by lines, commas, or semicolons.",
        key="copy_target_text",
    )
    if st.button("Build copy preview", type="primary", disabled=not target_text.strip() or not selected_source_groups):
        raw_targets = [v.strip().lower() for v in target_text.replace(",", "\n").replace(";", "\n").splitlines() if v.strip()]
        targets = list(dict.fromkeys(raw_targets))
        valid_targets = []
        invalid_targets = []
        for value in targets:
            try:
                valid_targets.append(normalize_lookup_email(value))
            except UserLookupError:
                invalid_targets.append(value)
        if invalid_targets:
            st.warning("Ignored invalid target emails: " + ", ".join(invalid_targets))
        if valid_targets:
            target_users = []
            with st.spinner("Checking target users in Adobe..."):
                for email in valid_targets:
                    try:
                        target_users.append(lookup_user(email))
                    except UserLookupError as exc:
                        st.error(f"{email}: {exc}")
                        target_users.append(None)
            st.session_state.copy_preview = build_copy_access_preview(
                source, target_users, valid_targets, selected_source_groups
            )
            st.session_state.copy_target_users = target_users
            st.session_state.copy_valid_targets = valid_targets
            st.session_state.copy_removed_groups = set()
            st.session_state.copy_last_request_id = None
            record(st.session_state.actor, "copy_access_preview", source.get("email", ""), selected_source_groups, "preview", f"Targets: {len(valid_targets)}")
            st.rerun()

    _render_preview_and_execute(source)


def _reset_preview_state() -> None:
    st.session_state.copy_preview = pd.DataFrame()
    st.session_state.copy_removed_groups = set()
    st.session_state.copy_target_users = []
    st.session_state.copy_valid_targets = []
    st.session_state.copy_last_request_id = None


def _build_target_user_table() -> pd.DataFrame:
    """Reconstruct the users dataframe execute() expects from the targets
    looked up when the preview was built — deriving a first/last name for any
    target that doesn't exist yet in Adobe (lookup_user() has nothing to
    derive it from since there's no Adobe record for a nonexistent user)."""
    rows = []
    for email, target in zip(st.session_state.get("copy_valid_targets", []), st.session_state.get("copy_target_users", [])):
        if target:
            first_name = target.get("first_name") or ""
            last_name = target.get("last_name") or ""
        else:
            parsed = derive_name(email)
            first_name, last_name = parsed.first_name, parsed.last_name
        rows.append({"email": email, "first_name": first_name, "last_name": last_name, "include": True})
    return pd.DataFrame(rows)


def _render_preview_and_execute(source: dict) -> None:
    copy_preview = st.session_state.get("copy_preview", pd.DataFrame())
    if copy_preview.empty:
        return

    removed = st.session_state.get("copy_removed_groups", set())
    active_preview = (
        copy_preview[~copy_preview["adobe_group_name"].isin(removed)]
        if "adobe_group_name" in copy_preview.columns else copy_preview
    )

    targets_count = int(active_preview["email"].nunique()) if not active_preview.empty else 0
    additions = int(active_preview["will_add"].sum()) if not active_preview.empty else 0
    already = int((~active_preview["will_add"]).sum()) if not active_preview.empty else 0
    c1, c2, c3 = st.columns(3)
    c1.metric("Targets", targets_count)
    c2.metric("Memberships to add", additions)
    c3.metric("Already assigned", already)

    only_changes = st.checkbox("Show only memberships that would be added", value=True, key="copy_only_changes")
    preview_view = active_preview[active_preview["will_add"]] if only_changes else active_preview
    st.dataframe(
        preview_view[["email", "target_status", "group_display_name", "system", "membership_status"]].rename(columns={
            "email": "Target email", "target_status": "User", "group_display_name": "Custom user group",
            "system": "System", "membership_status": "Result"
        }),
        width='stretch', hide_index=True,
    )
    st.download_button(
        "Export copy preview",
        safe_csv(active_preview.drop(columns=["will_add"])),
        "copy-access-preview.csv",
        "text/csv",
    )

    st.markdown("###### Groups included in this copy")
    st.caption("Remove a group here to leave it out of the test/execute below, without rebuilding the whole preview.")
    unique_groups = copy_preview[["adobe_group_name", "group_display_name", "system"]].drop_duplicates().sort_values("group_display_name")
    for _, g in unique_groups.iterrows():
        name = g["adobe_group_name"]
        is_removed = name in removed
        rc1, rc2, rc3, rc4 = st.columns([3, 2, 3, 1])
        rc1.write(f"{g['group_display_name']}{' (removed)' if is_removed else ''}")
        rc2.write(g["system"])
        rc3.write(name)
        if is_removed:
            if rc4.button("Restore", key=f"copy_restore_group_{name}"):
                st.session_state.copy_removed_groups = removed - {name}
                st.rerun()
        elif rc4.button("Remove", key=f"copy_remove_group_{name}"):
            st.session_state.copy_removed_groups = removed | {name}
            st.rerun()

    if active_preview.empty:
        st.warning("Every group has been removed from this copy — nothing left to test or execute.")
        return

    st.divider()
    st.markdown("##### Run test / Execute")
    groups_to_apply = sorted(active_preview["adobe_group_name"].unique().tolist())
    users_df = _build_target_user_table()

    if st.button(
        "Run test", type="secondary",
        help="Sends Adobe's action request with testOnly=true — Adobe validates the payload but makes no changes.",
    ):
        output = []
        with st.spinner("Testing against Adobe..."):
            for _, row in users_df.iterrows():
                email = str(row["email"])
                try:
                    result = run(client.provision(email, str(row["first_name"]), str(row["last_name"]), groups_to_apply, test_only=True))
                    status = "Test passed" if result["success"] else "Failed"
                    detail = f"Would create: {result['created']}; Groups: {', '.join(result['groups_added']) or 'None'}"
                    record(st.session_state.actor, "copy_access_test", email, groups_to_apply, status, detail)
                    output.append({"email": email, "status": status, "would_create": result["created"], "groups_to_add": "; ".join(result["groups_added"]), "details": detail})
                except Exception as exc:
                    record(st.session_state.actor, "copy_access_test", email, groups_to_apply, "Failed", str(exc))
                    output.append({"email": email, "status": "Failed", "would_create": False, "groups_to_add": "", "details": str(exc)})
        result_df = pd.DataFrame(output)
        st.dataframe(result_df, width='stretch', hide_index=True)
        st.download_button("Download test results", safe_csv(result_df), "copy-access-test-results.csv", "text/csv")

    if not settings.adobe_write_enabled:
        st.info(
            "Execute is disabled. Set ADOBE_WRITE_ENABLED=true in .env and restart the app to enable live "
            "writes — only after Run test payloads look correct. This can never be turned on from the UI."
        )
        return

    st.warning(
        f"⚠️ Live write mode is enabled. You are about to add **{additions} group assignment(s)** "
        f"across **{targets_count} target user(s)**. This makes real changes in Adobe. Running the "
        "same request again is safe — only missing changes are applied."
    )
    execute_confirm = st.checkbox(
        "I confirm this will make real changes in Adobe and I have reviewed the preview above.",
        key="copy_execute_confirm",
    )
    if st.button("⚠️ Execute (live Adobe changes)", type="primary", disabled=not execute_confirm):
        started_at = datetime.now(timezone.utc).isoformat()
        with st.spinner("Executing — this makes real changes in Adobe..."):
            results = execute(users_df, groups_to_apply, test_only=False)
        completed_at = datetime.now(timezone.utc).isoformat()
        exec_summary = execution_summary(results)
        if st.session_state.copy_last_request_id is None:
            st.session_state.copy_last_request_id = save_recent_request(
                st.session_state.actor, users_df.to_dict("records"), groups_to_apply, "Preview",
                {"users": len(users_df), "assignments": additions, "already": already, "failures": 0},
                None, f"Copy access from {source.get('email', '')}",
            )
        execution_id = save_execution(
            st.session_state.copy_last_request_id, st.session_state.actor,
            started_at, completed_at, test_only=False, results=results.to_dict("records"),
        )
        new_status = "Executed" if exec_summary["failed"] == 0 else (
            "Execution failed" if exec_summary["failed"] == len(results) else "Partially executed"
        )
        update_request_status(st.session_state.copy_last_request_id, new_status)
        for _, row in results.iterrows():
            detail = (
                f"Created: {row['created']}; Groups added: {', '.join(row['groups_added']) or 'None'}"
                if row["success"] else str(row["error"])
            )
            record(
                st.session_state.actor, "copy_access_execute", str(row["email"]),
                groups_to_apply, "Success" if row["success"] else "Failed", detail,
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
            with st.container(height=300):
                st.json(results[["email", "adobe_response"]].to_dict("records") if "adobe_response" in results.columns else [])
        st.download_button("Download execution CSV", safe_csv(display_results), f"copy-execution-{execution_id}.csv", "text/csv")
