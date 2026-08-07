from __future__ import annotations

import pandas as pd
import streamlit as st

from adobe_access.database import count_audit_events, read
from adobe_access.utils import safe_csv

_DISPLAY_LIMIT = 5000


def render() -> None:
    df = read(_DISPLAY_LIMIT)
    if df.empty:
        st.info("No audit records found.")
    else:
        total = count_audit_events()
        if total > len(df):
            st.caption(
                f"Showing the most recent {len(df)} of {total} total audit events — older "
                "events still exist in the database but aren't loaded into this view. "
                "Use the filters below to narrow within what's shown."
            )
        df["created_at_parsed"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
        c1, c2, c3, c4 = st.columns(4)
        query = c1.text_input("Search actor, user, action, group or details")
        actions = ["All"] + sorted(df["action"].dropna().unique().tolist())
        action = c2.selectbox("Action", actions)
        statuses = ["All"] + sorted(df["status"].dropna().unique().tolist())
        status_filter = c3.selectbox("Status", statuses)
        date_range = c4.date_input("Date range", value=())
        view = df.copy()
        if query:
            mask = view.drop(columns=["created_at_parsed"]).astype(str).apply(
                lambda column: column.str.contains(query, case=False, na=False)
            ).any(axis=1)
            view = view[mask]
        if action != "All":
            view = view[view["action"] == action]
        if status_filter != "All":
            view = view[view["status"] == status_filter]
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_date, end_date = date_range
            view = view[
                (view["created_at_parsed"].dt.date >= start_date)
                & (view["created_at_parsed"].dt.date <= end_date)
            ]
        a1, a2, a3 = st.columns(3)
        a1.metric("Matching events", len(view))
        a2.metric("Failures", int(view["status"].astype(str).str.contains("fail", case=False, na=False).sum()))
        a3.metric("Unique users", int(view["email"].replace("", pd.NA).dropna().nunique()))
        display = view.drop(columns=["created_at_parsed"])
        st.dataframe(display, width='stretch', hide_index=True)
        st.download_button("Download filtered audit CSV", safe_csv(display), "audit-history-filtered.csv", "text/csv")
