from __future__ import annotations

import pandas as pd
import streamlit as st

from adobe_access.database import record
from adobe_access.ui.shared import render_friendly_error, render_special_permissions
from adobe_access.users import (
    UserLookupError,
    compare_custom_group_memberships,
    compare_special_permissions,
    lookup_user,
    membership_table,
    special_permissions,
)
from adobe_access.utils import safe_csv


def render() -> None:
    st.subheader("Compare users")
    st.caption("Compare two Adobe users using only memberships in the synchronized custom user-group cache.")

    with st.form("compare_users_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        left_email = c1.text_input("First user email", value=st.session_state.get("compare_left_email_value", ""), placeholder="firstname.lastname@example.com")
        right_email = c2.text_input("Second user email", value=st.session_state.get("compare_right_email_value", ""), placeholder="firstname.lastname@example.com")
        compare_submitted = st.form_submit_button("Compare users", type="primary")
    compare_submitted = compare_submitted or st.session_state.pop("_retry_compare", False)

    if compare_submitted:
        st.session_state.compare_left_email_value = left_email
        st.session_state.compare_right_email_value = right_email
        try:
            with st.spinner("Looking up both users in Adobe..."):
                left_user = lookup_user(left_email)
                right_user = lookup_user(right_email)
            if not left_user or not right_user:
                missing = []
                if not left_user:
                    missing.append(left_email.strip().lower())
                if not right_user:
                    missing.append(right_email.strip().lower())
                st.session_state.compare_result = pd.DataFrame()
                st.error("User not found: " + ", ".join(missing))
            else:
                st.session_state.compare_left = left_user
                st.session_state.compare_right = right_user
                st.session_state.compare_left_email_value = left_user["email"]
                st.session_state.compare_right_email_value = right_user["email"]
                st.session_state.compare_result = compare_custom_group_memberships(left_user, right_user)
                record(st.session_state.actor, "compare-users", f"{left_user['email']} | {right_user['email']}", [], "Success", "Compared synchronized custom user groups")
        except UserLookupError as exc:
            st.session_state.compare_result = pd.DataFrame()
            if render_friendly_error(exc, key="retry_compare", context="While looking up users to compare."):
                st.session_state["_retry_compare"] = True
                st.rerun()

    left_user = st.session_state.get("compare_left")
    right_user = st.session_state.get("compare_right")
    comparison = st.session_state.get("compare_result", pd.DataFrame())
    if left_user and right_user:
        left_special = special_permissions(left_user)
        right_special = special_permissions(right_user)
        l1, l2 = st.columns(2)
        with l1:
            st.markdown(f"### {left_user.get('display_name') or left_user.get('email')}")
            st.caption(left_user.get("email", ""))
            lm1, lm2 = st.columns(2)
            lm1.metric("Custom groups", len(membership_table(left_user)))
            lm2.metric("Special permissions", len(left_special))
            render_special_permissions(left_special, key_prefix="compare_left")
        with l2:
            st.markdown(f"### {right_user.get('display_name') or right_user.get('email')}")
            st.caption(right_user.get("email", ""))
            rm1, rm2 = st.columns(2)
            rm1.metric("Custom groups", len(membership_table(right_user)))
            rm2.metric("Special permissions", len(right_special))
            render_special_permissions(right_special, key_prefix="compare_right")

        special_comparison = compare_special_permissions(left_user, right_user)
        if not special_comparison.empty:
            st.markdown("###### ⚠️ Special permissions — side-by-side")
            st.caption("Org-level administrative roles — read live from Adobe, not the synced custom-group cache.")
            display_special = special_comparison.copy()
            display_special["detail"] = display_special["detail"].where(display_special["detail"] != "", display_special["raw"])
            st.dataframe(
                display_special.drop(columns=["raw"]).rename(columns={
                    "category": "Role", "detail": "Product / detail",
                    "left_member": left_user.get("email", "First user"),
                    "right_member": right_user.get("email", "Second user"),
                    "comparison": "Result",
                }),
                width='stretch', hide_index=True,
            )

        if comparison.empty:
            st.info("Neither user has memberships in the synchronized custom user-group cache.")
        else:
            shared = int((comparison["comparison"] == "Shared").sum())
            only_left = int((comparison["comparison"] == "Only first user").sum())
            only_right = int((comparison["comparison"] == "Only second user").sum())
            m1, m2, m3 = st.columns(3)
            m1.metric("Shared", shared)
            m2.metric("Only first user", only_left)
            m3.metric("Only second user", only_right)

            f1, f2, f3 = st.columns([3, 2, 2])
            query = f1.text_input("Filter groups", key="compare_group_filter")
            systems = ["All"] + sorted(comparison["system"].dropna().astype(str).unique().tolist())
            system_filter = f2.selectbox("System", systems, key="compare_system_filter")
            comparison_filter = f3.selectbox("Membership", ["Differences only", "All", "Shared", "Only first user", "Only second user"], key="compare_membership_filter")
            view = comparison.copy()
            if query:
                view = view[
                    view["display_name"].str.contains(query, case=False, na=False)
                    | view["adobe_group_name"].str.contains(query, case=False, na=False)
                ]
            if system_filter != "All":
                view = view[view["system"] == system_filter]
            if comparison_filter == "Differences only":
                view = view[view["comparison"] != "Shared"]
            elif comparison_filter != "All":
                view = view[view["comparison"] == comparison_filter]

            display = view.rename(columns={
                "display_name": "Custom user group",
                "system": "System",
                "adobe_group_name": "Adobe group",
                "left_member": left_user.get("email", "First user"),
                "right_member": right_user.get("email", "Second user"),
                "comparison": "Result",
            })
            st.dataframe(display, width='stretch', hide_index=True)
            export = comparison.rename(columns={
                "display_name": "group_display_name",
                "system": "system",
                "adobe_group_name": "adobe_group_name",
                "left_member": left_user.get("email", "first_user"),
                "right_member": right_user.get("email", "second_user"),
                "comparison": "comparison",
            })
            st.download_button("Export full comparison", safe_csv(export), "custom-user-group-comparison.csv", "text/csv")
