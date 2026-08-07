from __future__ import annotations

import streamlit as st

from adobe_access import diagnostics, settings_store
from adobe_access.config import settings
from adobe_access.database import catalog_status, record
from adobe_access.ui.shared import render_friendly_error


def render() -> None:
    status = catalog_status()
    st.subheader("Settings")
    st.caption(
        "Non-secret operational settings can be changed here and take effect immediately. "
        "Adobe credentials and write-mode always stay in .env — see below."
    )

    values = settings_store.current_values()
    overridden = settings_store.overridden_keys()
    field_help = {field.key: field.help for field in settings_store.FIELDS}
    identity_options = ["federatedID", "enterpriseID", "adobeID"]

    with st.form("settings_form"):
        domains_value = st.text_input(
            "Allowed email domains", value=str(values["allowed_email_domains"]),
            help=field_help["allowed_email_domains"],
        )
        c1, c2 = st.columns(2)
        country_value = c1.text_input(
            "Default country", value=str(values["default_country"]), help=field_help["default_country"],
        )
        identity_default = str(values["default_identity_type"])
        identity_value = c2.selectbox(
            "Default identity type", identity_options,
            index=identity_options.index(identity_default) if identity_default in identity_options else 0,
            help=field_help["default_identity_type"],
        )
        c3, c4 = st.columns(2)
        ttl_value = c3.number_input(
            "Cache TTL (seconds)", min_value=30, max_value=86400, step=30,
            value=int(values["cache_ttl_seconds"]), help=field_help["cache_ttl_seconds"],
        )
        auto_validate_value = c4.toggle(
            "Auto Adobe validation", value=bool(values["auto_adobe_validation"]),
            help=field_help["auto_adobe_validation"],
        )
        save_col, reset_col = st.columns([1, 4])
        saved = save_col.form_submit_button("Save settings", type="primary")
        reset_clicked = reset_col.form_submit_button("Reset to .env defaults")

    if saved:
        country_clean = country_value.strip().upper()
        if not (len(country_clean) == 2 and country_clean.isalpha()):
            st.error(
                f"'{country_value}' isn't a two-letter country code (e.g. US, CA, GB). "
                "Settings were not saved — a bad value here would only surface later as "
                "an Adobe rejection when creating a new user."
            )
        else:
            settings_store.save(
                {
                    "allowed_email_domains": domains_value,
                    "default_country": country_clean,
                    "default_identity_type": identity_value,
                    "cache_ttl_seconds": int(ttl_value),
                    "auto_adobe_validation": auto_validate_value,
                },
                st.session_state.actor,
            )
            record(st.session_state.actor, "settings-update", "", [], "Success", "Non-secret operational settings updated")
            st.toast("Settings saved.")
            st.rerun()
    if reset_clicked:
        settings_store.reset()
        record(st.session_state.actor, "settings-reset", "", [], "Success", "Reset to .env defaults")
        st.toast("Settings reset to .env defaults.")
        st.rerun()
    if overridden:
        st.caption(f"Overridden from .env: {', '.join(sorted(overridden))}")

    st.divider()
    st.markdown("#### Adobe connection")
    a1, a2, a3 = st.columns(3)
    a1.metric("Mode", "Mock" if settings.mock_adobe else ("Live write" if settings.adobe_write_enabled else "Live read/test"))
    a2.metric("Adobe configured", "Yes" if settings.adobe_configured else "No")
    a3.metric("Cached groups", status["group_count"])
    test_clicked = st.button("Test Adobe connection")
    test_clicked = test_clicked or st.session_state.pop("_retry_settings_test", False)
    if test_clicked:
        with st.spinner("Contacting Adobe..."):
            result = diagnostics.check_adobe_connection()
        record(
            st.session_state.actor, "connection-test", "", [],
            "Success" if result["success"] else "Failed", str(result["detail"]),
        )
        if result["success"]:
            st.success(f"Connected. {result['detail']}")
        elif render_friendly_error(
            RuntimeError(str(result["detail"])), key="retry_settings_test",
            context="While testing the Adobe connection.",
        ):
            st.session_state["_retry_settings_test"] = True
            st.rerun()

    st.divider()
    st.markdown("#### Secrets (read-only — managed in .env)")
    st.json({
        "adobe_org_id": settings.adobe_org_id or "(not set)",
        "adobe_umapi_base_url": settings.adobe_umapi_base_url,
        "adobe_write_enabled": settings.adobe_write_enabled,
        "app_env": settings.app_env,
    })
    st.info(
        "Adobe credentials and ADOBE_WRITE_ENABLED are never editable from this page — "
        "update .env and restart the app to change them. That keeps write mode from being "
        "flipped on by accident from the UI."
    )
