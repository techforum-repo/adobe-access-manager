from __future__ import annotations

import pandas as pd
import streamlit as st

from adobe_access.client import client
from adobe_access.database import record, replace_managed_users, user_catalog_status
from adobe_access.provisioning import build_user_table, run
from adobe_access.ui.shared import render_friendly_error
from adobe_access.users import (
    UserLookupError,
    browse_cached_users,
    get_cached_user,
    lookup_user,
    membership_table,
    special_permissions,
    user_export_table,
)
from adobe_access.utils import safe_csv


def render() -> None:
    st.subheader("User search")
    st.caption(
        "Search Adobe directly for one exact email, or browse a locally synced "
        "directory (including a blank search) without hitting Adobe on every keystroke."
    )

    _render_sync_header()

    tab_search, tab_browse = st.tabs(["Search Adobe (exact)", "Browse synced users"])
    with tab_search:
        _render_exact_search()
    with tab_browse:
        _render_browse_cached()


def _render_sync_header() -> None:
    status = user_catalog_status()
    c1, c2, c3 = st.columns([2, 1, 2])
    sync_requested = c1.button("Sync users from Adobe", type="primary") or st.session_state.pop("_retry_user_sync", False)
    c2.metric("Cached users", status["user_count"])
    c3.metric("Last sync", status["synced_at"] or "Never")
    if sync_requested:
        try:
            with st.spinner("Reading the Adobe user directory and replacing the local cache..."):
                result = replace_managed_users(run(client.list_users()))
            record(st.session_state.actor, "user-cache-replace", "", [], "Success", str(result))
            st.success(f"Sync complete. Cached {result['users']} users.")
            st.rerun()
        except Exception as exc:
            record(st.session_state.actor, "user-cache-replace", "", [], "Failed", str(exc))
            if render_friendly_error(exc, key="retry_user_sync", context="While syncing the user directory from Adobe."):
                st.session_state["_retry_user_sync"] = True
                st.rerun()
    st.divider()


def _render_exact_search() -> None:
    with st.form("user_lookup_form", clear_on_submit=False):
        lookup_email = st.text_input(
            "User email",
            value=st.session_state.user_search_email_value,
            placeholder="firstname.lastname@example.com",
        )
        search_submitted = st.form_submit_button("Search Adobe", type="primary")
    search_submitted = search_submitted or st.session_state.pop("_retry_user_search", False)

    if search_submitted:
        st.session_state.user_search_email_value = lookup_email.strip().lower()
        try:
            with st.spinner("Looking up the user in Adobe..."):
                found_user = lookup_user(lookup_email)
            st.session_state.user_search_result = found_user
            record(
                st.session_state.actor,
                "user-lookup",
                lookup_email.strip().lower(),
                [],
                "Found" if found_user else "Not found",
                "Exact Adobe user lookup",
            )
        except UserLookupError as exc:
            st.session_state.user_search_result = None
            if render_friendly_error(exc, key="retry_user_search", context=f"While looking up {lookup_email.strip() or 'the user'}."):
                st.session_state["_retry_user_search"] = True
                st.rerun()

    searched_email = st.session_state.user_search_email_value
    user = st.session_state.user_search_result
    if searched_email and user is None:
        st.warning(f"No Adobe user was found for {searched_email}.")
        if st.button("Prepare as a new provisioning request", type="secondary"):
            st.session_state.users = build_user_table([searched_email])
            st.session_state.selected_groups = []
            st.session_state.preview = pd.DataFrame()
            st.session_state.provision_step = 2
            st.session_state.pending_navigation = "Provision access"
            st.rerun()

    if user:
        _render_user_detail(user, key_prefix="search")


def _render_browse_cached() -> None:
    query = st.text_input(
        "Search cached users",
        placeholder="Leave blank to show everyone synced",
        key="user_browse_query",
    )
    results = browse_cached_users(query)
    if results.empty:
        if query:
            st.info("No cached users match that search.")
        else:
            st.info("No users are cached yet. Click \"Sync users from Adobe\" above.")
        return

    st.caption(f"{len(results)} cached user(s).")
    display = results.rename(columns={
        "email": "Email", "display_name": "Name", "identity_type": "Identity type",
        "status": "Status", "custom_group_count": "Custom groups",
    })
    st.dataframe(display, width='stretch', hide_index=True)
    st.download_button("Export CSV", safe_csv(display), "cached-users.csv", "text/csv")

    st.markdown("##### View details")
    selected_email = st.selectbox(
        "Pick a cached user", results["email"].tolist(),
        format_func=lambda email: (
            f"{results.loc[results['email'] == email, 'display_name'].iloc[0]} · {email}"
        ),
        key="user_browse_selected",
    )
    if selected_email:
        cached_user = get_cached_user(selected_email)
        if cached_user:
            _render_user_detail(cached_user, key_prefix="browse")


def _render_user_detail(user: dict, *, key_prefix: str) -> None:
    """Shared detail view for both the exact-search result and a cached-browse
    drill-down — same shape (email/first_name/last_name/identity_type/status/
    groups/display_name) from either lookup_user() or get_cached_user()."""
    name = user.get("display_name") or user.get("email") or "Unknown user"
    st.markdown(f"### {name}")
    st.caption(str(user.get("email") or ""))
    special = special_permissions(user)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Identity type", user.get("identity_type") or "Unknown")
    c2.metric("Status", user.get("status") or "Unknown")
    memberships = membership_table(user)
    c3.metric("Custom user groups", len(memberships))
    c4.metric("Special permissions", len(special))
    ignored = memberships.attrs.get("ignored_non_custom_memberships", 0)
    if ignored:
        st.caption(f"{ignored} other Adobe membership(s) not shown — not in the synced custom-group cache.")

    if not special.empty:
        st.markdown("###### ⚠️ Special permissions")
        st.caption("Org-level administrative roles — read live from Adobe, not the synced custom-group cache.")
        st.dataframe(
            special.rename(columns={"role": "Role", "adobe_group_name": "Adobe name"}),
            width='stretch', hide_index=True,
        )

    if memberships.empty:
        st.info("This user has no memberships in the locally synchronized Adobe custom user groups.")
    else:
        system_options = ["All"] + sorted(memberships["system"].dropna().astype(str).unique().tolist())
        f1, f2 = st.columns([3, 2])
        membership_query = f1.text_input("Filter memberships", key=f"{key_prefix}_membership_filter")
        membership_system = f2.selectbox("System", system_options, key=f"{key_prefix}_membership_system")
        membership_view = memberships.copy()
        if membership_query:
            membership_view = membership_view[
                membership_view["display_name"].str.contains(membership_query, case=False, na=False)
                | membership_view["adobe_group_name"].str.contains(membership_query, case=False, na=False)
            ]
        if membership_system != "All":
            membership_view = membership_view[membership_view["system"] == membership_system]

        display_memberships = membership_view[["display_name", "system", "adobe_group_name"]].rename(columns={
            "display_name": "Display name",
            "system": "System",
            "adobe_group_name": "Adobe user group",
        })
        st.dataframe(display_memberships, width='stretch', hide_index=True)

    export_df = user_export_table(user, memberships)
    a1, a2 = st.columns([1, 3])
    a1.download_button(
        "Export custom groups",
        safe_csv(export_df),
        f"{user.get('email', 'adobe-user')}-custom-groups.csv",
        "text/csv",
        width='stretch',
        key=f"{key_prefix}_export",
    )
    if a2.button("Provision additional access", type="primary", width='content', key=f"{key_prefix}_provision"):
        st.session_state.users = build_user_table([str(user.get("email") or "")])
        st.session_state.selected_groups = []
        st.session_state.preview = pd.DataFrame()
        st.session_state.provision_step = 2
        st.session_state.pending_navigation = "Provision access"
        st.rerun()
