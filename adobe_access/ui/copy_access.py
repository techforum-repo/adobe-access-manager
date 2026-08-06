from __future__ import annotations

import pandas as pd
import streamlit as st

from adobe_access.database import record
from adobe_access.ui.shared import render_friendly_error
from adobe_access.utils import safe_csv
from adobe_access.users import (
    UserLookupError,
    build_copy_access_preview,
    lookup_user,
    membership_table,
    normalize_lookup_email,
)


def render() -> None:
    st.subheader("Copy access")
    st.caption("Preview copying synchronized Adobe custom user groups from one source user to one or more target users. No Adobe changes are made.")

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
                st.session_state.copy_preview = pd.DataFrame()
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
    if source:
        source_memberships = membership_table(source)
        st.success(f"Loaded {source.get('display_name') or source.get('email')} with {len(source_memberships)} synchronized custom user groups.")
        if source_memberships.empty:
            st.info("The source user has no memberships in the locally synchronized custom user-group cache.")
        else:
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
                    record(st.session_state.actor, "copy_access_preview", source.get("email", ""), selected_source_groups, "preview", f"Targets: {len(valid_targets)}")

            copy_preview = st.session_state.get("copy_preview", pd.DataFrame())
            if not copy_preview.empty:
                targets_count = copy_preview["email"].nunique()
                additions = int(copy_preview["will_add"].sum())
                already = int((~copy_preview["will_add"]).sum())
                c1, c2, c3 = st.columns(3)
                c1.metric("Targets", targets_count)
                c2.metric("Memberships to add", additions)
                c3.metric("Already assigned", already)

                only_changes = st.checkbox("Show only memberships that would be added", value=True, key="copy_only_changes")
                preview_view = copy_preview[copy_preview["will_add"]] if only_changes else copy_preview
                st.dataframe(
                    preview_view[["email", "target_status", "group_display_name", "system", "membership_status"]].rename(columns={
                        "email": "Target email", "target_status": "User", "group_display_name": "Custom user group",
                        "system": "System", "membership_status": "Result"
                    }),
                    width='stretch', hide_index=True,
                )
                st.download_button(
                    "Export copy preview",
                    safe_csv(copy_preview.drop(columns=["will_add"])),
                    "copy-access-preview.csv",
                    "text/csv",
                )
                st.info("Preview only. No users or memberships have been changed in Adobe.")
