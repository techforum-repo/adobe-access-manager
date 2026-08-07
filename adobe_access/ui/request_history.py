from __future__ import annotations

import pandas as pd
import streamlit as st

from adobe_access.database import (
    count_recent_requests,
    get_execution,
    get_recent_request,
    list_executions_for_request,
    list_recent_requests,
)
from adobe_access.ui.shared import reuse_request
from adobe_access.utils import safe_csv

_DISPLAY_LIMIT = 1000


def render() -> None:
    st.subheader("Request history")
    st.caption("Every preview built from Provision access is saved here. Search, reopen, reuse into the wizard, or export.")

    requests_df = list_recent_requests(_DISPLAY_LIMIT)
    if requests_df.empty:
        st.info("No requests have been saved yet. Build a preview from Provision access to create one.")
    else:
        total = count_recent_requests()
        if total > len(requests_df):
            st.caption(
                f"Showing the most recent {len(requests_df)} of {total} total requests — older "
                "requests still exist but aren't loaded into this view. Use the filters below to "
                "narrow within what's shown."
            )
        requests_df["created_at_parsed"] = pd.to_datetime(requests_df["created_at"], errors="coerce", utc=True)
        f1, f2, f3, f4 = st.columns(4)
        query = f1.text_input("Search requester, template, or status", key="request_history_query")
        requesters = ["All"] + sorted(requests_df["actor"].dropna().unique().tolist())
        requester_filter = f2.selectbox("Requester", requesters, key="request_history_requester")
        statuses = ["All"] + sorted(requests_df["status"].dropna().unique().tolist())
        status_filter = f3.selectbox("Status", statuses, key="request_history_status")
        date_range = f4.date_input("Date range", value=(), key="request_history_dates")

        view = requests_df.copy()
        if query:
            mask = (
                view["actor"].astype(str).str.contains(query, case=False, na=False)
                | view["template_name"].astype(str).str.contains(query, case=False, na=False)
                | view["status"].astype(str).str.contains(query, case=False, na=False)
            )
            view = view[mask]
        if requester_filter != "All":
            view = view[view["actor"] == requester_filter]
        if status_filter != "All":
            view = view[view["status"] == status_filter]
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_date, end_date = date_range
            view = view[
                (view["created_at_parsed"].dt.date >= start_date)
                & (view["created_at_parsed"].dt.date <= end_date)
            ]

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Matching requests", len(view))
        m2.metric("Total users", int(view["user_count"].sum()) if not view.empty else 0)
        m3.metric("Assignments to add", int(view.get("summary_assignments", pd.Series(dtype=float)).fillna(0).sum()))
        m4.metric("With failures", int((view.get("summary_failures", pd.Series(dtype=float)).fillna(0) > 0).sum()))

        display = view.rename(columns={
            "id": "Request ID", "created_at": "Timestamp", "actor": "Requester",
            "template_name": "Template", "user_count": "Users", "group_count": "Groups",
            "status": "Status", "summary_assignments": "To add",
            "summary_already": "Already assigned", "summary_failures": "Failures",
        }).copy()
        display["Template"] = display["Template"].replace("", "Manual groups")
        display_columns = [c for c in ["Request ID", "Timestamp", "Requester", "Template", "Users", "Groups", "Status", "To add", "Already assigned", "Failures"] if c in display.columns]
        st.dataframe(display[display_columns], width='stretch', hide_index=True)
        st.download_button("Export filtered CSV", safe_csv(display[display_columns]), "request-history.csv", "text/csv")

        st.markdown("##### Open a request")
        if view.empty:
            st.info("No requests match the current filters.")
        else:
            selected_id = st.selectbox(
                "Request ID",
                view["id"].astype(int).tolist(),
                format_func=lambda value: (
                    f"#{value} · {view.loc[view['id'] == value, 'template_name'].iloc[0] or 'Manual groups'}"
                    f" · {view.loc[view['id'] == value, 'created_at'].iloc[0]}"
                ),
                key="request_history_selected",
            )
            request = get_recent_request(int(selected_id))
            if request:
                with st.container(border=True):
                    st.markdown(f"**Request #{request['id']}** · {request['created_at']} · {request['status']}")
                    st.caption(f"Requester: {request['actor']} · Template: {request['template_name'] or 'Manual groups'}")
                    summary = request.get("summary", {})
                    users_df = pd.DataFrame(request["users"])
                    s1, s2, s3, s4, s5 = st.columns(5)
                    s1.metric("Users", summary.get("users", len(users_df)))
                    s2.metric("Existing", summary.get("existing", 0))
                    s3.metric("New", summary.get("new", 0))
                    s4.metric("To add", summary.get("assignments", 0))
                    s5.metric("Already assigned", summary.get("already", 0))
                    st.markdown("**Users**")
                    if users_df.empty:
                        st.caption("No users on this request.")
                    else:
                        st.dataframe(users_df, width='stretch', hide_index=True)
                    st.markdown("**Groups**")
                    st.write(", ".join(request["groups"]) or "None")

                    executions_df = list_executions_for_request(int(request["id"]))
                    if not executions_df.empty:
                        st.markdown("**Executions**")
                        exec_display = executions_df[[
                            "id", "started_at", "test_only", "status", "duration_ms",
                            "created_count", "existing_count", "groups_added_count",
                            "already_assigned_count", "failed_count", "retry_count_total",
                        ]].rename(columns={
                            "id": "Execution ID", "started_at": "Started", "test_only": "Test only",
                            "status": "Status", "duration_ms": "Duration (ms)",
                            "created_count": "Created", "existing_count": "Existing",
                            "groups_added_count": "Groups added", "already_assigned_count": "Already assigned",
                            "failed_count": "Failed", "retry_count_total": "Retries",
                        })
                        st.dataframe(exec_display, width='stretch', hide_index=True)
                        with st.expander("Per-user execution detail (including Adobe response)"):
                            for _, execution_row in executions_df.iterrows():
                                execution_detail = get_execution(int(execution_row["id"]))
                                st.markdown(f"**Execution #{execution_detail['id']}** · {execution_detail['started_at']} · {execution_detail['status']}")
                                st.json(execution_detail["results"])

                    a1, a2 = st.columns([1, 4])
                    if a1.button("Reuse in wizard", type="primary", key=f"reuse_open_{request['id']}"):
                        reuse_request(int(request["id"]))
                        st.rerun()
                    a2.download_button(
                        "Export this request",
                        safe_csv(users_df) if not users_df.empty else "",
                        f"request-{request['id']}-users.csv", "text/csv",
                        key=f"export_open_{request['id']}",
                    )
