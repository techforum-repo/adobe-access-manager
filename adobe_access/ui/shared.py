from __future__ import annotations

"""State, navigation, and widgets shared across every page in adobe_access/ui/*.

Nothing page-specific lives here — if only one page needs it, it belongs in
that page's own module instead.
"""

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from adobe_access.config import settings
from adobe_access.database import get_recent_request, read_managed_groups
from adobe_access.errors import friendly_error

PAGE_NAMES = [
    "Dashboard", "Provision access", "Templates", "User groups", "User search",
    "Compare users", "Copy access", "Request history", "Audit history",
    "Diagnostics", "Settings",
]

DEFAULT_STATE = {
    "actor": "local.user@example.com",
    "users": pd.DataFrame(),
    "selected_groups": [],
    "preview": pd.DataFrame(),
    "provision_step": 1,
    "user_search_result": None,
    "user_search_email_value": "",
    "copy_source": None,
    "copy_preview": pd.DataFrame(),
    "copy_target_text": "",
    "compare_left": None,
    "compare_right": None,
    "compare_result": pd.DataFrame(),
    "active_template_id": None,
    "template_mode": "Browse",
    "template_manage_id": None,
    "pending_navigation": None,
    "validation_checked": False,
    "last_request_id": None,
}

CUSTOM_CSS = """<style>
.block-container{max-width:1450px;padding-top:1.35rem}
.hero{padding:1.1rem 1.35rem;border:1px solid #ddd;border-radius:16px;margin-bottom:1rem}
[data-testid=stMetric]{border:1px solid #ddd;padding:1rem;border-radius:14px}
.badge{padding:.25rem .55rem;border:1px solid #ccc;border-radius:999px;font-size:.8rem}
.step{padding:.6rem .8rem;border:1px solid #ddd;border-radius:10px;text-align:center;font-weight:600}
</style>"""


def init_session_state() -> None:
    for key, value in DEFAULT_STATE.items():
        if key not in st.session_state:
            st.session_state[key] = value


def apply_pending_navigation() -> None:
    """Streamlit does not allow changing a widget-backed session key after the
    widget is created, so a page that wants to redirect elsewhere sets
    `pending_navigation` and calls st.rerun(); this must run before the
    sidebar radio widget (key="navigation") is constructed on the next run."""
    pending_navigation = st.session_state.get("pending_navigation")
    if pending_navigation:
        st.session_state["navigation"] = pending_navigation
        st.session_state["pending_navigation"] = None


def render_sidebar() -> str:
    with st.sidebar:
        st.markdown("## 🔐 Adobe Access Manager")
        page = st.radio(
            "Navigation", PAGE_NAMES, label_visibility="collapsed", key="navigation",
        )
        st.divider()
        st.text_input("Signed in as", key="actor")
        mode = "Mock" if settings.mock_adobe else ("Live write" if settings.adobe_write_enabled else "Live read/test")
        st.markdown(f"<span class='badge'>{mode}</span>", unsafe_allow_html=True)
    return page


def render_hero() -> None:
    st.markdown(
        "<div class='hero'><h1>Adobe Access Manager</h1><p>Validate users, preview access, "
        "and manage Adobe custom user groups with a complete audit trail.</p></div>",
        unsafe_allow_html=True,
    )


def reset_provisioning() -> None:
    st.session_state.users = pd.DataFrame()
    st.session_state.selected_groups = []
    st.session_state.preview = pd.DataFrame()
    st.session_state.provision_step = 1
    st.session_state.validation_checked = False
    st.session_state.last_request_id = None
    reset_group_picker("provision")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def reuse_request(request_id: int) -> None:
    """Load a saved request back into the Provision Wizard at the Validate step."""
    request = get_recent_request(request_id)
    if not request:
        return
    st.session_state.users = pd.DataFrame(request["users"])
    st.session_state.selected_groups = list(request["groups"])
    st.session_state.preview = pd.DataFrame()
    st.session_state.provision_step = 2
    st.session_state.validation_checked = False
    st.session_state.active_template_id = request.get("template_id")
    st.session_state.pending_navigation = "Provision access"
    # The Access step's group multiselect is keyed ("provision_selected") and Streamlit
    # ignores group_picker()'s `default=` once that key exists — without this, the
    # widget would keep showing whatever was selected in a *previous* wizard run
    # instead of this request's groups, and could even crash if a stale system
    # filter excludes one of them from the current options.
    reset_group_picker("provision")


def render_friendly_error(exc: Exception, *, key: str, context: str = "") -> bool:
    """Show a plain-language error box instead of a raw traceback/string.

    Returns True if the user clicked Retry, so the caller can re-run the
    action inline (e.g. `if render_friendly_error(exc, key="x"): st.rerun()`).
    """
    info = friendly_error(exc)
    with st.container(border=True):
        st.error(f"**{info.title}**")
        if context:
            st.caption(context)
        if info.reasons:
            st.markdown("Possible reasons:\n\n" + "\n".join(f"- {reason}" for reason in info.reasons))
        with st.expander("Technical details"):
            st.code(str(exc) or "(no message)")
        if info.retryable:
            return st.button("Retry", key=key)
    return False


def group_catalog() -> pd.DataFrame:
    return read_managed_groups()


def reset_group_picker(key_prefix: str) -> None:
    """Clear a group_picker()'s widget state (search, system filter, selection).

    Needed whenever the *set of defaults* changes across reruns — e.g. switching
    from editing one template to another — because a widget's `key=` makes
    Streamlit ignore `default=` after the first render; without this, the
    previous template's selected groups silently stick around.
    """
    for suffix in ("_search", "_systems", "_selected"):
        st.session_state.pop(f"{key_prefix}{suffix}", None)


def group_picker(groups: pd.DataFrame, key_prefix: str, defaults: list[str] | None = None) -> list[str]:
    if groups.empty:
        st.info("The local group cache is empty. Open User groups and select Sync from Adobe.")
        return []
    work = groups.rename(columns={"adobe_group_name": "name"}).copy()
    c1, c2 = st.columns([3, 2])
    query = c1.text_input("Search custom user groups", key=f"{key_prefix}_search")
    systems = sorted(work["system"].dropna().astype(str).unique().tolist())
    selected_systems = c2.multiselect("Filter systems", systems, default=systems, key=f"{key_prefix}_systems")
    if query:
        mask = (
            work["name"].str.contains(query, case=False, na=False)
            | work["display_name"].str.contains(query, case=False, na=False)
            | work["description"].str.contains(query, case=False, na=False)
        )
        work = work[mask]
    if systems and selected_systems:
        work = work[work["system"].isin(selected_systems)]
    elif systems:
        work = work.iloc[0:0]
    options = work["name"].drop_duplicates().tolist()
    labels = {
        str(row["adobe_group_name"]): " · ".join(
            part for part in [
                str(row.get("display_name") or row["adobe_group_name"]),
                str(row.get("system") or ""),
                "PRIVILEGED" if bool(row.get("privileged", False)) else "",
            ] if part
        )
        for _, row in groups.iterrows()
    }
    selected = st.multiselect(
        "Adobe custom user groups",
        options,
        default=[value for value in (defaults or []) if value in options],
        format_func=lambda value: labels.get(value, value),
        key=f"{key_prefix}_selected",
    )
    privileged = groups[
        groups["adobe_group_name"].isin(selected) & (groups["privileged"] == True)  # noqa: E712
    ]
    if not privileged.empty:
        st.warning("Privileged groups selected: " + ", ".join(privileged["display_name"].tolist()))
    st.caption(f"Showing {len(options)} of {len(groups)} cached custom user groups.")
    return selected
