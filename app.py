from __future__ import annotations

import streamlit as st

from adobe_access.database import initialize
from adobe_access.ui import (
    audit_history,
    compare_users,
    copy_access,
    dashboard,
    diagnostics_page,
    provision,
    request_history,
    settings_page,
    templates_page,
    user_groups,
    user_search,
)
from adobe_access.ui.shared import (
    CUSTOM_CSS,
    apply_pending_navigation,
    init_session_state,
    render_hero,
    render_sidebar,
)

initialize()
st.set_page_config(page_title="Adobe Access Manager", page_icon="🔐", layout="wide")
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

init_session_state()

# Streamlit does not allow changing a widget-backed session key after the widget
# is created, so this must run before render_sidebar() builds the nav radio.
apply_pending_navigation()
page = render_sidebar()
render_hero()

PAGES = {
    "Dashboard": dashboard.render,
    "Provision access": provision.render,
    "Templates": templates_page.render,
    "User groups": user_groups.render,
    "User search": user_search.render,
    "Compare users": compare_users.render,
    "Copy access": copy_access.render,
    "Request history": request_history.render,
    "Audit history": audit_history.render,
    "Diagnostics": diagnostics_page.render,
    "Settings": settings_page.render,
}
PAGES[page]()
