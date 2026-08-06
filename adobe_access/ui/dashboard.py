from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from adobe_access import settings_store
from adobe_access.config import settings
from adobe_access.database import (
    catalog_status,
    last_connection_check,
    list_favorite_groups,
    list_recent_requests,
    most_used_templates,
    read,
    requests_today_failed_count,
    workflow_summary,
)
from adobe_access.ui.shared import group_catalog, parse_iso, reuse_request


def render() -> None:
    status = catalog_status()
    workflow = workflow_summary(st.session_state.actor)
    connection = last_connection_check()
    failed_today = requests_today_failed_count()

    st.markdown("##### Overview")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    if connection is None:
        c1.metric("Adobe connection", "Not checked")
    else:
        c1.metric("Adobe connection", "Connected" if connection["success"] else "Failed")
        checked_dt = parse_iso(connection["checked_at"])
        c1.caption(f"as of {checked_dt.strftime('%Y-%m-%d %H:%M UTC') if checked_dt else connection['checked_at']}")
    c2.metric("Cached user groups", status["group_count"])
    synced_dt = parse_iso(status["synced_at"])
    stale = bool(synced_dt and (datetime.now(timezone.utc) - synced_dt).total_seconds() > settings_store.cache_ttl_seconds())
    c3.metric("Last Adobe sync", status["synced_at"][:16].replace("T", " ") if status["synced_at"] else "Never")
    if stale:
        c3.caption("⚠️ Stale — past cache TTL")
    c4.metric("Templates", workflow["template_count"])
    c5.metric("Today's requests", workflow["requests_today"])
    c6.metric("Failed requests today", failed_today)
    st.caption(f"Mode: {'Mock' if settings.mock_adobe else ('Live write' if settings.adobe_write_enabled else 'Live read/test')} · Dashboard never calls Adobe directly — sync and connection checks are manual.")

    st.markdown("##### Quick actions")
    q1, q2, q3, q4 = st.columns(4)
    if q1.button("Provision access", width='stretch'):
        st.session_state.pending_navigation = "Provision access"
        st.rerun()
    if q2.button("User search", width='stretch'):
        st.session_state.pending_navigation = "User search"
        st.rerun()
    if q3.button("Sync user groups", width='stretch'):
        st.session_state.pending_navigation = "User groups"
        st.rerun()
    if q4.button("Create template", width='stretch'):
        st.session_state.pending_navigation = "Templates"
        st.rerun()

    st.divider()
    left, right = st.columns([3, 2])
    with left:
        st.markdown("##### Recent requests")
        recent = list_recent_requests(8)
        if recent.empty:
            st.info("No provisioning previews have been saved yet.")
        else:
            for _, item in recent.iterrows():
                cols = st.columns([3, 2, 1, 1])
                # Template names are user-controlled free text (set on the Templates
                # page) — never interpolate them into unsafe_allow_html markdown.
                # Plain st.markdown()/st.caption() auto-escape instead of rendering HTML.
                label = item.get("template_name") or "Manual groups"
                cols[0].markdown(f"**{label}**")
                cols[0].caption(f"{item['user_count']} users · {item['group_count']} groups")
                cols[1].caption(f"{item['created_at']} · {item['status']}")
                cols[2].metric("To add", int(item.get("summary_assignments", 0) or 0))
                if cols[3].button("Reuse", key=f"reuse_request_{int(item['id'])}"):
                    reuse_request(int(item["id"]))
                    st.rerun()
            if st.button("View all request history"):
                st.session_state.pending_navigation = "Request history"
                st.rerun()

        st.markdown("##### Recent activity")
        audit = read(10)
        if audit.empty:
            st.info("No activity has been recorded yet.")
        else:
            st.dataframe(audit[["created_at", "actor", "action", "email", "status"]], width='stretch', hide_index=True)

    with right:
        st.markdown("##### Favorite groups")
        favorites = list_favorite_groups(st.session_state.actor)
        if not favorites:
            st.info("No favorite groups yet. Pin some from User groups.")
        else:
            catalog = group_catalog()
            labels = catalog.set_index("adobe_group_name")["display_name"].to_dict() if not catalog.empty else {}
            for name in favorites:
                st.markdown(f"⭐ {labels.get(name, name)}")

        st.markdown("##### Most used templates")
        top_templates = most_used_templates(5)
        if top_templates.empty:
            st.info("No templates have been used in a saved request yet.")
        else:
            st.dataframe(
                top_templates.rename(columns={"template_name": "Template", "uses": "Times used"}),
                width='stretch', hide_index=True,
            )
