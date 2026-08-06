from __future__ import annotations

import streamlit as st

from adobe_access.client import client
from adobe_access.database import (
    catalog_status,
    list_favorite_groups,
    record,
    replace_favorite_groups,
    replace_managed_groups,
)
from adobe_access.provisioning import run
from adobe_access.ui.shared import group_catalog, render_friendly_error
from adobe_access.utils import safe_csv


def render() -> None:
    status = catalog_status()
    c1, c2, c3 = st.columns([2, 1, 2])
    sync_requested = c1.button("Sync from Adobe", type="primary") or st.session_state.pop("_retry_group_sync", False)
    c2.metric("Cached groups", status["group_count"])
    c3.metric("Last sync", status["synced_at"] or "Never")
    if sync_requested:
        try:
            with st.spinner("Reading Adobe custom user groups and replacing the local cache..."):
                result = replace_managed_groups(run(client.list_groups()))
            record(st.session_state.actor, "group-cache-replace", "", [], "Success", str(result))
            st.success(f"Sync complete. Cached {result['groups']} groups.")
            st.rerun()
        except Exception as exc:
            record(st.session_state.actor, "group-cache-replace", "", [], "Failed", str(exc))
            if render_friendly_error(exc, key="retry_sync", context="While syncing user groups from Adobe."):
                st.session_state["_retry_group_sync"] = True
                st.rerun()
    groups = group_catalog()
    if groups.empty:
        st.info("The local group cache is empty.")
    else:
        favorites = list_favorite_groups(st.session_state.actor)
        with st.expander(f"Favorite groups ({len(favorites)})", expanded=False):
            favorite_options = groups["adobe_group_name"].tolist()
            favorite_labels = {
                row["adobe_group_name"]: f"{row['display_name']} · {row['system']}"
                for _, row in groups.iterrows()
            }
            updated_favorites = st.multiselect(
                "Groups shown first during provisioning",
                favorite_options,
                default=[group for group in favorites if group in favorite_options],
                format_func=lambda value: favorite_labels.get(value, value),
                key="favorite_group_editor",
            )
            if st.button("Save favorites", type="primary"):
                replace_favorite_groups(st.session_state.actor, updated_favorites)
                record(st.session_state.actor, "favorites-update", "", updated_favorites, "Success", f"{len(updated_favorites)} groups")
                st.toast("Favorite groups saved.")
                st.rerun()
        a, b = st.columns([4, 2])
        query = a.text_input("Search user groups")
        systems = ["All"] + sorted(groups["system"].dropna().astype(str).unique().tolist())
        system = b.selectbox("System", systems)
        view = groups.copy()
        if query:
            mask = (
                view["display_name"].str.contains(query, case=False, na=False)
                | view["adobe_group_name"].str.contains(query, case=False, na=False)
                | view["description"].str.contains(query, case=False, na=False)
            )
            view = view[mask]
        if system != "All":
            view = view[view["system"] == system]
        display = view[["display_name", "system", "description", "privileged", "member_count"]].rename(columns={"display_name": "name"})
        st.dataframe(display, width='stretch', hide_index=True)
        st.download_button("Download CSV", safe_csv(display), "adobe-user-groups.csv", "text/csv")
