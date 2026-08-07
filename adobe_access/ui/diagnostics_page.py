from __future__ import annotations

import pandas as pd
import streamlit as st

from adobe_access import diagnostics
from adobe_access.database import last_connection_check, record, sqlite_health, table_counts
from adobe_access.ui.shared import render_friendly_error


def render() -> None:
    st.subheader("Diagnostics")
    st.caption("Operational health for administrators. Nothing on this page mutates Adobe data.")

    env = diagnostics.environment_info()
    e1, e2, e3, e4 = st.columns(4)
    e1.metric("Version", env["version"])
    e2.metric("Mode", env["mode"])
    e3.metric("Adobe configured", "Yes" if env["adobe_configured"] else "No")
    e4.metric("Cached groups", env["cached_groups"])

    st.markdown("##### Adobe connection")
    connection = last_connection_check()
    if connection:
        badge = "🟢 Connected" if connection["success"] else "🔴 Failed"
        st.write(f"{badge} · last checked {connection['checked_at']} · mode: {connection['mode']}")
        with st.expander("Last check detail"):
            st.code(str(connection["detail"]))
    else:
        st.info("Adobe connectivity has not been checked yet in this environment.")
    check_clicked = st.button("Check Adobe connection now", type="primary")
    check_clicked = check_clicked or st.session_state.pop("_retry_diagnostics_check", False)
    if check_clicked:
        with st.spinner("Contacting Adobe..."):
            result = diagnostics.check_adobe_connection()
        record(
            st.session_state.actor, "diagnostics-connection-check", "", [],
            "Success" if result["success"] else "Failed", str(result["detail"]),
        )
        if result["success"]:
            st.success(f"Connected. {result['detail']}")
        elif render_friendly_error(
            RuntimeError(str(result["detail"])), key="retry_diagnostics_check",
            context="While checking the Adobe connection.",
        ):
            st.session_state["_retry_diagnostics_check"] = True
            st.rerun()

    st.markdown("##### OAuth / configuration status")
    st.json({
        "adobe_configured": env["adobe_configured"],
        "adobe_org_id": env["adobe_org_id"],
        "adobe_umapi_base_url": env["adobe_umapi_base_url"],
        "last_group_sync": env["last_group_sync"],
    })

    st.markdown("##### Cache size")
    counts = table_counts()
    st.dataframe(
        pd.DataFrame(sorted(counts.items()), columns=["Table", "Rows"]),
        width='stretch', hide_index=True,
    )

    st.markdown("##### SQLite health")
    health = sqlite_health()
    h1, h2, h3 = st.columns(3)
    h1.metric("Integrity", health["integrity"])
    h2.metric("Size", f"{health['size_bytes'] / 1024:.0f} KB")
    h3.metric("Healthy", "Yes" if health["ok"] else "No")
    st.caption(f"Database path: {health['path']}")

    st.markdown("##### Environment")
    st.json({
        "app_env": env["app_env"],
        "python_version": env["python_version"],
        "platform": env["platform"],
        "streamlit_version": env["streamlit_version"],
        "pandas_version": env["pandas_version"],
    })

    st.markdown("##### Logs")
    # Read the 500-line tail once, up front — the on-page view only shows the
    # last 200 of it, and the bundle below reuses the same read instead of
    # opening and reading the log file a second time.
    log_lines = diagnostics.log_tail(500).splitlines()
    if log_lines:
        with st.expander("Recent log lines (last 200)"):
            st.code("\n".join(log_lines[-200:]), height=350)
    else:
        st.caption("No log file yet — one is created the first time an action is recorded.")
    st.download_button(
        "Download diagnostics bundle (JSON)",
        diagnostics.diagnostics_bundle(
            environment=env, sqlite=health, counts=counts, connection=connection, log_lines=log_lines,
        ),
        "diagnostics-bundle.json",
        "application/json",
    )
