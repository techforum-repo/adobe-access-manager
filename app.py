from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from adobe_access import __version__
from adobe_access.client import client
from adobe_access.config import settings
from adobe_access.database import (
    audit_summary,
    catalog_status,
    initialize,
    read,
    read_managed_groups,
    record,
    replace_managed_groups,
    list_favorite_groups,
    replace_favorite_groups,
    save_recent_request,
    list_recent_requests,
    get_recent_request,
    workflow_summary,
)
from adobe_access.provisioning import (
    build_user_table, compare_users, preview, run,
    validate_users_against_adobe, preview_summary,
)
from adobe_access.users import (
    UserLookupError,
    build_copy_access_preview,
    compare_custom_group_memberships,
    lookup_user,
    membership_table,
    normalize_lookup_email,
    user_export_table,
)
from adobe_access.templates import (
    TemplateValidationError,
    create_template,
    delete_template,
    duplicate_template,
    get_template,
    list_templates,
    update_template,
)

initialize()
st.set_page_config(page_title="Adobe Access Manager", page_icon="🔐", layout="wide")
st.markdown(
    """<style>
    .block-container{max-width:1450px;padding-top:1.35rem}
    .hero{padding:1.1rem 1.35rem;border:1px solid #ddd;border-radius:16px;margin-bottom:1rem}
    [data-testid=stMetric]{border:1px solid #ddd;padding:1rem;border-radius:14px}
    .badge{padding:.25rem .55rem;border:1px solid #ccc;border-radius:999px;font-size:.8rem}
    .step{padding:.6rem .8rem;border:1px solid #ddd;border-radius:10px;text-align:center;font-weight:600}
    </style>""",
    unsafe_allow_html=True,
)

DEFAULT_STATE = {
    "actor": "local.user@bsci.com",
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
    "template_mode": "Create",
    "pending_navigation": None,
    "validation_checked": False,
    "last_request_id": None,
}
for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value


@st.cache_data(ttl=300, show_spinner=False)
def cached_users():
    return run(client.list_users())


def reset_provisioning() -> None:
    st.session_state.users = pd.DataFrame()
    st.session_state.selected_groups = []
    st.session_state.preview = pd.DataFrame()
    st.session_state.provision_step = 1
    st.session_state.validation_checked = False
    st.session_state.last_request_id = None


def group_catalog() -> pd.DataFrame:
    return read_managed_groups()


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


# Streamlit does not allow changing a widget-backed session key after the widget is created.
# Apply deferred navigation before constructing the sidebar radio.
pending_navigation = st.session_state.get("pending_navigation")
if pending_navigation:
    st.session_state["navigation"] = pending_navigation
    st.session_state["pending_navigation"] = None

with st.sidebar:
    st.markdown("## 🔐 Adobe Access Manager")
    page = st.radio(
        "Navigation",
        ["Dashboard", "Provision access", "Templates", "User groups", "User search", "Compare users", "Copy access", "Audit history", "Settings"],
        label_visibility="collapsed",
        key="navigation",
    )
    st.divider()
    st.text_input("Signed in as", key="actor")
    mode = "Mock" if settings.mock_adobe else ("Live write" if settings.adobe_write_enabled else "Live read/test")
    st.markdown(f"<span class='badge'>{mode}</span>", unsafe_allow_html=True)

st.markdown(
    "<div class='hero'><h1>Adobe Access Manager</h1><p>Validate users, preview access, and manage Adobe custom user groups with a complete audit trail.</p></div>",
    unsafe_allow_html=True,
)

if page == "Dashboard":
    status = catalog_status()
    summary = audit_summary()
    workflow = workflow_summary(st.session_state.actor)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Cached user groups", status["group_count"])
    c2.metric("Templates", workflow["template_count"])
    c3.metric("Today's requests", workflow["requests_today"])
    c4.metric("Favorite groups", workflow["favorite_count"])
    c5.metric("Failed audit actions", summary["failed"])
    st.caption(f"Last group sync: {status['synced_at'] or 'Never'} · Mode: {'Mock' if settings.mock_adobe else ('Live write' if settings.adobe_write_enabled else 'Live read/test')}")

    recent = list_recent_requests(8)
    st.subheader("Recent requests")
    if recent.empty:
        st.info("No provisioning previews have been saved yet.")
    else:
        for _, item in recent.iterrows():
            cols = st.columns([3, 2, 1, 1])
            label = item.get("template_name") or "Manual groups"
            cols[0].markdown(f"**{label}**<br>{item['user_count']} users · {item['group_count']} groups", unsafe_allow_html=True)
            cols[1].caption(f"{item['created_at']} · {item['status']}")
            cols[2].metric("To add", int(item.get("summary_assignments", 0) or 0))
            if cols[3].button("Reuse", key=f"reuse_request_{int(item['id'])}"):
                request = get_recent_request(int(item["id"]))
                st.session_state.users = pd.DataFrame(request["users"])
                st.session_state.selected_groups = list(request["groups"])
                st.session_state.preview = pd.DataFrame()
                st.session_state.provision_step = 2
                st.session_state.validation_checked = False
                st.session_state.active_template_id = request.get("template_id")
                st.session_state.pending_navigation = "Provision access"
                st.rerun()

    audit = read(10)
    st.subheader("Recent activity")
    if audit.empty:
        st.info("No activity has been recorded yet.")
    else:
        st.dataframe(audit[["created_at", "actor", "action", "email", "status"]], use_container_width=True, hide_index=True)

elif page == "User groups":
    status = catalog_status()
    c1, c2, c3 = st.columns([2, 1, 2])
    sync_requested = c1.button("Sync from Adobe", type="primary")
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
            st.error(str(exc))
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
        st.dataframe(display, use_container_width=True, hide_index=True)
        st.download_button("Download CSV", display.to_csv(index=False), "adobe-user-groups.csv", "text/csv")

elif page == "Provision access":
    steps = st.columns(4)
    for index, title in enumerate(["1. Users", "2. Validate", "3. Access", "4. Review"], start=1):
        marker = "✓" if st.session_state.provision_step > index else ("●" if st.session_state.provision_step == index else "○")
        steps[index - 1].markdown(f"<div class='step'>{marker} {title}</div>", unsafe_allow_html=True)
    st.write("")

    if st.session_state.provision_step == 1:
        source = st.radio("Input method", ["Paste emails", "Upload CSV/XLSX"], horizontal=True)
        emails: list[str] = []
        if source == "Paste emails":
            text = st.text_area("One email per line, or separated by comma/semicolon", height=160)
            emails = [item.strip() for item in text.replace(",", "\n").replace(";", "\n").splitlines() if item.strip()]
        else:
            upload = st.file_uploader("Upload a file containing an email column", type=["csv", "xlsx"])
            if upload:
                uploaded_df = pd.read_csv(upload) if Path(upload.name).suffix.lower() == ".csv" else pd.read_excel(upload)
                column = next((c for c in uploaded_df.columns if str(c).lower().strip() in {"email", "email_address", "user"}), None)
                if column:
                    emails = uploaded_df[column].dropna().astype(str).tolist()
                else:
                    st.error("No email column was found.")
        if st.button("Validate and continue", type="primary", disabled=not emails):
            st.session_state.users = build_user_table(emails)
            st.session_state.validation_checked = False
            st.session_state.provision_step = 2
            st.rerun()

    elif st.session_state.provision_step == 2:
        st.subheader("Validate users")
        st.caption("Local validation is shown immediately. Valid users are checked against Adobe automatically when this step opens.")

        valid_before_lookup = st.session_state.users[st.session_state.users["validation"] == "Valid"]
        if not st.session_state.validation_checked and not valid_before_lookup.empty:
            with st.spinner("Automatically checking users in Adobe..."):
                st.session_state.users = validate_users_against_adobe(st.session_state.users)
            st.session_state.validation_checked = True

        disabled_columns = ["validation", "notes"]
        if "adobe_status" in st.session_state.users.columns:
            disabled_columns += ["adobe_status", "current_group_count", "lookup_details"]
        st.session_state.users = st.data_editor(
            st.session_state.users,
            use_container_width=True,
            hide_index=True,
            disabled=disabled_columns,
        )
        invalid = st.session_state.users[st.session_state.users["validation"] != "Valid"]
        valid = st.session_state.users[st.session_state.users["validation"] == "Valid"]
        duplicate_count = int((st.session_state.users["validation"] == "Duplicate").sum())
        invalid_count = int((st.session_state.users["validation"] == "Invalid").sum())
        v1, v2, v3 = st.columns(3)
        v1.metric("Valid rows", len(valid))
        v2.metric("Duplicates", duplicate_count)
        v3.metric("Invalid", invalid_count)
        if not invalid.empty:
            st.download_button("Download excluded rows", invalid.to_csv(index=False), "excluded-users.csv", "text/csv")
        if st.session_state.validation_checked and not valid.empty:
            if st.button("Refresh Adobe validation", help="Run the Adobe lookup again after editing user details or after a temporary lookup failure."):
                with st.spinner("Refreshing Adobe user validation..."):
                    st.session_state.users = validate_users_against_adobe(st.session_state.users)
                st.rerun()
        if st.session_state.validation_checked and "adobe_status" in st.session_state.users.columns:
            statuses = st.session_state.users["adobe_status"].value_counts().to_dict()
            st.success(
                f"Adobe validation completed: {statuses.get('Existing', 0)} existing, "
                f"{statuses.get('New', 0)} new, {statuses.get('Lookup failed', 0)} lookup failures."
            )
        b1, b2 = st.columns([1, 4])
        if b1.button("Back"):
            st.session_state.provision_step = 1
            st.rerun()
        if b2.button("Continue to access", type="primary", disabled=not bool(st.session_state.users["include"].any())):
            st.session_state.provision_step = 3
            st.rerun()

    elif st.session_state.provision_step == 3:
        groups = group_catalog()
        templates = list_templates()
        if not templates.empty:
            template_options = {int(row["id"]): str(row["name"]) for _, row in templates.iterrows()}
            selected_template_id = st.selectbox(
                "Access template",
                options=[None] + list(template_options),
                format_func=lambda value: "No template" if value is None else template_options[value],
                key="provision_template_id",
            )
            if selected_template_id is not None:
                template = get_template(int(selected_template_id))
                st.caption(
                    f"{template['system']} · {template['group_count']} groups"
                    + (f" · {template['description']}" if template.get('description') else "")
                )
                if st.button("Apply template", type="secondary"):
                    applied_groups = list(template["groups"])
                    st.session_state.selected_groups = applied_groups
                    st.session_state["provision_selected"] = applied_groups
                    st.session_state.active_template_id = int(selected_template_id)
                    st.toast(f"Applied template: {template['name']}")
                    st.rerun()
        else:
            st.caption("No access templates are available. Create one from Templates.")
        favorites = [group for group in list_favorite_groups(st.session_state.actor) if group in groups.get("adobe_group_name", pd.Series(dtype=str)).tolist()]
        if favorites:
            with st.expander(f"Favorite groups ({len(favorites)})", expanded=True):
                favorite_rows = groups[groups["adobe_group_name"].isin(favorites)]
                favorite_labels = {row["adobe_group_name"]: f"{row['display_name']} · {row['system']}" for _, row in favorite_rows.iterrows()}
                add_favorites = st.multiselect(
                    "Quick add favorites",
                    favorites,
                    format_func=lambda value: favorite_labels.get(value, value),
                    key="provision_favorite_quick_add",
                )
                if st.button("Add selected favorites") and add_favorites:
                    st.session_state.selected_groups = list(dict.fromkeys(st.session_state.selected_groups + add_favorites))
                    st.session_state["provision_selected"] = st.session_state.selected_groups
                    st.rerun()
        st.session_state.selected_groups = group_picker(groups, "provision", st.session_state.selected_groups)
        if st.session_state.active_template_id:
            active_template = get_template(int(st.session_state.active_template_id))
            if active_template:
                template_groups = list(active_template.get("groups", []))
                st.info(f"Template applied: {active_template['name']}. You can still add or remove groups.")
                with st.expander(f"Groups from template ({len(template_groups)})", expanded=True):
                    if template_groups:
                        template_group_rows = []
                        group_lookup = groups.set_index("adobe_group_name").to_dict("index") if not groups.empty else {}
                        for group_name in template_groups:
                            metadata = group_lookup.get(group_name, {})
                            template_group_rows.append({
                                "Display name": metadata.get("display_name") or group_name,
                                "System": metadata.get("system") or active_template.get("system") or "Other",
                                "Adobe user group": group_name,
                            })
                        st.dataframe(pd.DataFrame(template_group_rows), use_container_width=True, hide_index=True)
                    else:
                        st.caption("This template does not contain any groups.")
        b1, b2 = st.columns([1, 4])
        if b1.button("Back"):
            st.session_state.provision_step = 2
            st.rerun()
        if b2.button("Build preview", type="primary", disabled=not st.session_state.selected_groups):
            with st.spinner("Checking users and current memberships in Adobe..."):
                st.session_state.preview = preview(st.session_state.users, st.session_state.selected_groups)
            summary = preview_summary(st.session_state.preview)
            template = get_template(int(st.session_state.active_template_id)) if st.session_state.active_template_id else None
            request_users = st.session_state.users.to_dict("records")
            st.session_state.last_request_id = save_recent_request(
                st.session_state.actor,
                request_users,
                st.session_state.selected_groups,
                "Preview",
                summary,
                int(st.session_state.active_template_id) if st.session_state.active_template_id else None,
                template.get("name", "") if template else "",
            )
            record(st.session_state.actor, "provision-preview", "", st.session_state.selected_groups, "Success", str(summary))
            st.session_state.provision_step = 4
            st.rerun()

    else:
        summary = preview_summary(st.session_state.preview)
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Users", summary["users"])
        m2.metric("Existing", summary["existing"])
        m3.metric("New", summary["new"])
        m4.metric("Assignments to add", summary["assignments"])
        m5.metric("Already assigned", summary["already"])
        failed = summary["failures"]
        if len(st.session_state.preview) <= 12:
            for _, user_row in st.session_state.preview.iterrows():
                with st.container(border=True):
                    c1, c2 = st.columns([2, 3])
                    c1.markdown(f"**{user_row.get('name') or user_row['email']}**")
                    c1.caption(user_row["email"])
                    c1.write(user_row["user_action"])
                    c2.markdown(f"**Will add:** {user_row['groups_to_add']}")
                    c2.caption(f"Already assigned: {user_row['already_assigned']}")
                    if user_row.get("lookup") != "OK":
                        c2.error(str(user_row.get("lookup")))
        else:
            st.dataframe(st.session_state.preview, use_container_width=True, hide_index=True)
        st.download_button("Download preview", st.session_state.preview.to_csv(index=False), "provision-preview.csv", "text/csv")
        test_only = st.checkbox(
            "Adobe test mode (no changes)",
            value=True,
            disabled=not settings.adobe_write_enabled,
            help="Until write mode is enabled, execution is always sent as Adobe testOnly=true.",
        )
        confirm = st.checkbox("I reviewed the users and selected groups")
        c1, c2, c3 = st.columns([1, 2, 3])
        if c1.button("Back"):
            st.session_state.provision_step = 3
            st.rerun()
        if c2.button("Start over"):
            reset_provisioning()
            st.rerun()
        if c3.button("Run test", type="primary", disabled=not confirm or failed > 0):
            output = []
            for _, row in st.session_state.users[st.session_state.users["include"] == True].iterrows():  # noqa: E712
                email = str(row["email"])
                try:
                    result = run(client.provision(email, str(row["first_name"]), str(row["last_name"]), st.session_state.selected_groups, test_only=True))
                    status = "Test passed" if result["success"] else "Failed"
                    detail = f"Would create: {result['created']}; Groups: {', '.join(result['groups_added']) or 'None'}"
                    record(st.session_state.actor, "provision-test", email, st.session_state.selected_groups, status, detail)
                    output.append({"email": email, "status": status, "would_create": result["created"], "groups_to_add": "; ".join(result["groups_added"]), "details": detail})
                except Exception as exc:
                    record(st.session_state.actor, "provision-test", email, st.session_state.selected_groups, "Failed", str(exc))
                    output.append({"email": email, "status": "Failed", "would_create": False, "groups_to_add": "", "details": str(exc)})
            result_df = pd.DataFrame(output)
            st.dataframe(result_df, use_container_width=True, hide_index=True)
            st.download_button("Download results", result_df.to_csv(index=False), "provision-test-results.csv", "text/csv")

elif page == "User search":
    st.subheader("User search")
    st.caption("Look up an Adobe user by exact email address and review their current custom user-group memberships.")

    with st.form("user_lookup_form", clear_on_submit=False):
        lookup_email = st.text_input(
            "User email",
            value=st.session_state.user_search_email_value,
            placeholder="firstname.lastname@bsci.com",
        )
        search_submitted = st.form_submit_button("Search Adobe", type="primary")

    if search_submitted:
        try:
            with st.spinner("Looking up the user in Adobe..."):
                found_user = lookup_user(lookup_email)
            st.session_state.user_search_email_value = lookup_email.strip().lower()
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
            st.error(str(exc))

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
        name = user.get("display_name") or user.get("email") or "Unknown user"
        st.markdown(f"### {name}")
        st.caption(str(user.get("email") or ""))
        c1, c2, c3 = st.columns(3)
        c1.metric("Identity type", user.get("identity_type") or "Unknown")
        c2.metric("Status", user.get("status") or "Unknown")
        memberships = membership_table(user)
        c3.metric("Custom user groups", len(memberships))

        if memberships.empty:
            st.info("This user has no memberships in the locally synchronized Adobe custom user groups.")
        else:
            system_options = ["All"] + sorted(memberships["system"].dropna().astype(str).unique().tolist())
            f1, f2 = st.columns([3, 2])
            membership_query = f1.text_input("Filter memberships", key="user_membership_filter")
            membership_system = f2.selectbox("System", system_options, key="user_membership_system")
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
            st.dataframe(display_memberships, use_container_width=True, hide_index=True)

        export_df = user_export_table(user, memberships)
        a1, a2 = st.columns([1, 3])
        a1.download_button(
            "Export custom groups",
            export_df.to_csv(index=False),
            f"{user.get('email', 'adobe-user')}-custom-groups.csv",
            "text/csv",
            use_container_width=True,
        )
        if a2.button("Provision additional access", type="primary", use_container_width=False):
            st.session_state.users = build_user_table([str(user.get("email") or "")])
            st.session_state.selected_groups = []
            st.session_state.preview = pd.DataFrame()
            st.session_state.provision_step = 2
            st.session_state.pending_navigation = "Provision access"
            st.rerun()

elif page == "Compare users":
    st.subheader("Compare users")
    st.caption("Compare two Adobe users using only memberships in the synchronized custom user-group cache.")

    with st.form("compare_users_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        left_email = c1.text_input("First user email", value=st.session_state.get("compare_left_email_value", ""), placeholder="firstname.lastname@bsci.com")
        right_email = c2.text_input("Second user email", value=st.session_state.get("compare_right_email_value", ""), placeholder="firstname.lastname@bsci.com")
        compare_submitted = st.form_submit_button("Compare users", type="primary")

    if compare_submitted:
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
            st.error(str(exc))

    left_user = st.session_state.get("compare_left")
    right_user = st.session_state.get("compare_right")
    comparison = st.session_state.get("compare_result", pd.DataFrame())
    if left_user and right_user:
        l1, l2 = st.columns(2)
        with l1:
            st.markdown(f"### {left_user.get('display_name') or left_user.get('email')}")
            st.caption(left_user.get("email", ""))
            st.metric("Custom groups", len(membership_table(left_user)))
        with l2:
            st.markdown(f"### {right_user.get('display_name') or right_user.get('email')}")
            st.caption(right_user.get("email", ""))
            st.metric("Custom groups", len(membership_table(right_user)))

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
            st.dataframe(display, use_container_width=True, hide_index=True)
            export = comparison.rename(columns={
                "display_name": "group_display_name",
                "system": "system",
                "adobe_group_name": "adobe_group_name",
                "left_member": left_user.get("email", "first_user"),
                "right_member": right_user.get("email", "second_user"),
                "comparison": "comparison",
            })
            st.download_button("Export full comparison", export.to_csv(index=False), "custom-user-group-comparison.csv", "text/csv")

elif page == "Copy access":
    st.subheader("Copy access")
    st.caption("Preview copying synchronized Adobe custom user groups from one source user to one or more target users. No Adobe changes are made.")

    source_email = st.text_input("Source user email", value=st.session_state.get("copy_source_email", ""), key="copy_source_email")
    if st.button("Load source user", type="primary", disabled="@" not in source_email):
        try:
            source = lookup_user(source_email)
            if source is None:
                st.session_state.copy_source = None
                st.error("The source user was not found in Adobe.")
            else:
                st.session_state.copy_source = source
                st.session_state.copy_preview = pd.DataFrame()
        except UserLookupError as exc:
            st.error(str(exc))

    source = st.session_state.get("copy_source")
    if source:
        source_memberships = membership_table(source)
        st.success(f"Loaded {source.get('display_name') or source.get('email')} with {len(source_memberships)} synchronized custom user groups.")
        if source_memberships.empty:
            st.info("The source user has no memberships in the locally synchronized custom user-group cache.")
        else:
            source_options = source_memberships["adobe_group_name"].tolist()
            labels = {row["adobe_group_name"]: f"{row['display_name']} · {row['system']}" for _, row in source_memberships.iterrows()}
            selected_source_groups = st.multiselect(
                "Groups to copy",
                source_options,
                default=source_options,
                format_func=lambda name: labels.get(name, name),
                key="copy_selected_groups",
            )
            with st.expander("Source custom user groups", expanded=False):
                st.dataframe(
                    source_memberships[["display_name", "system", "adobe_group_name"]].rename(columns={
                        "display_name": "Display name", "system": "System", "adobe_group_name": "Adobe group"
                    }),
                    use_container_width=True, hide_index=True,
                )

            target_text = st.text_area(
                "Target users",
                value=st.session_state.get("copy_target_text", ""),
                placeholder="firstname.lastname@bsci.com\nsecond.user@bsci.com",
                help="Enter one or more email addresses separated by lines, commas, or semicolons.",
                key="copy_target_text",
            )
            if st.button("Build copy preview", type="primary", disabled=not target_text.strip() or not selected_source_groups):
                raw_targets = [v.strip().lower() for v in target_text.replace(",", "\n").replace(";", "\n").splitlines() if v.strip()]
                targets = list(dict.fromkeys(raw_targets))
                valid_targets = []
                invalid_targets = []
                for value in targets:
                    try:
                        valid_targets.append(normalize_lookup_email(value))
                    except UserLookupError:
                        invalid_targets.append(value)
                if invalid_targets:
                    st.warning("Ignored invalid target emails: " + ", ".join(invalid_targets))
                if valid_targets:
                    target_users = []
                    with st.spinner("Checking target users in Adobe..."):
                        for email in valid_targets:
                            try:
                                target_users.append(lookup_user(email))
                            except UserLookupError as exc:
                                st.error(f"{email}: {exc}")
                                target_users.append(None)
                    st.session_state.copy_preview = build_copy_access_preview(
                        source, target_users, valid_targets, selected_source_groups
                    )
                    record(st.session_state.actor, "copy_access_preview", source.get("email", ""), selected_source_groups, "preview", f"Targets: {len(valid_targets)}")

            copy_preview = st.session_state.get("copy_preview", pd.DataFrame())
            if not copy_preview.empty:
                targets_count = copy_preview["email"].nunique()
                additions = int(copy_preview["will_add"].sum())
                already = int((~copy_preview["will_add"]).sum())
                c1, c2, c3 = st.columns(3)
                c1.metric("Targets", targets_count)
                c2.metric("Memberships to add", additions)
                c3.metric("Already assigned", already)

                only_changes = st.checkbox("Show only memberships that would be added", value=True, key="copy_only_changes")
                preview_view = copy_preview[copy_preview["will_add"]] if only_changes else copy_preview
                st.dataframe(
                    preview_view[["email", "target_status", "group_display_name", "system", "membership_status"]].rename(columns={
                        "email": "Target email", "target_status": "User", "group_display_name": "Custom user group",
                        "system": "System", "membership_status": "Result"
                    }),
                    use_container_width=True, hide_index=True,
                )
                st.download_button(
                    "Export copy preview",
                    copy_preview.drop(columns=["will_add"]).to_csv(index=False),
                    "copy-access-preview.csv",
                    "text/csv",
                )
                st.info("Preview only. No users or memberships have been changed in Adobe.")

elif page == "Templates":
    groups = group_catalog()
    templates = list_templates()
    st.subheader("Access templates")
    st.caption("Create reusable bundles of Adobe custom user groups for the Provision Access workflow.")

    top1, top2, top3 = st.columns([3, 2, 1])
    template_query = top1.text_input("Search templates")
    systems = ["All"] + sorted(templates["system"].dropna().astype(str).unique().tolist()) if not templates.empty else ["All"]
    template_system = top2.selectbox("System", systems)
    if top3.button("New template", type="primary", use_container_width=True):
        st.session_state.template_mode = "Create"
        st.session_state.active_template_id = None
        st.session_state.pop("template_form_groups", None)
        st.rerun()

    filtered_templates = templates.copy()
    if not filtered_templates.empty:
        if template_query:
            mask = (
                filtered_templates["name"].str.contains(template_query, case=False, na=False)
                | filtered_templates["description"].str.contains(template_query, case=False, na=False)
            )
            filtered_templates = filtered_templates[mask]
        if template_system != "All":
            filtered_templates = filtered_templates[filtered_templates["system"] == template_system]

    if filtered_templates.empty:
        st.info("No templates match the current filters." if not templates.empty else "No templates have been created.")
    else:
        table = filtered_templates[["name", "system", "description", "group_count", "updated_at"]].rename(
            columns={"name": "Template", "system": "System", "description": "Description", "group_count": "Groups", "updated_at": "Updated"}
        )
        st.dataframe(table, use_container_width=True, hide_index=True)

        selected_id = st.selectbox(
            "Select a template to view or manage",
            filtered_templates["id"].astype(int).tolist(),
            format_func=lambda value: str(filtered_templates.loc[filtered_templates["id"] == value, "name"].iloc[0]),
            key="template_selected_id",
        )
        selected_template = get_template(int(selected_id))
        with st.expander("Template groups", expanded=True):
            st.write(selected_template.get("description") or "No description")
            st.caption(f"System: {selected_template['system']} · {selected_template['group_count']} groups")
            st.dataframe(pd.DataFrame({"Adobe user group": selected_template["groups"]}), use_container_width=True, hide_index=True)

        a1, a2, a3 = st.columns(3)
        if a1.button("Edit", use_container_width=True):
            st.session_state.template_mode = "Edit"
            st.session_state.active_template_id = int(selected_id)
            st.session_state.pop("template_form_groups", None)
            st.rerun()
        if a2.button("Duplicate", use_container_width=True):
            st.session_state.template_mode = "Duplicate"
            st.session_state.active_template_id = int(selected_id)
            st.rerun()
        if a3.button("Delete", use_container_width=True):
            st.session_state.template_mode = "Delete"
            st.session_state.active_template_id = int(selected_id)
            st.rerun()

    mode = st.session_state.template_mode
    active = get_template(int(st.session_state.active_template_id)) if st.session_state.active_template_id else None

    if mode in {"Create", "Edit"}:
        title = "Create template" if mode == "Create" else f"Edit template: {active['name']}"
        with st.expander(title, expanded=(mode == "Edit" or templates.empty)):
            default_name = active["name"] if active else ""
            default_description = active["description"] if active else ""
            default_system = active["system"] if active else "Other"
            default_groups = active["groups"] if active else []

            form_name = st.text_input("Template name", value=default_name, key=f"template_name_{mode}_{st.session_state.active_template_id}")
            form_description = st.text_area("Description", value=default_description, key=f"template_description_{mode}_{st.session_state.active_template_id}")
            catalog_systems = sorted(set(groups["system"].dropna().astype(str).tolist()) | {default_system, "Other"}) if not groups.empty else [default_system, "Other"]
            form_system = st.selectbox(
                "System",
                catalog_systems,
                index=catalog_systems.index(default_system) if default_system in catalog_systems else 0,
                key=f"template_system_{mode}_{st.session_state.active_template_id}",
            )
            selected_groups = group_picker(groups, "template_form", default_groups)
            save_col, cancel_col = st.columns([1, 4])
            if save_col.button("Save", type="primary", use_container_width=True):
                try:
                    if mode == "Create":
                        template_id = create_template(form_name, form_description, form_system, selected_groups, st.session_state.actor)
                        action = "template-create"
                    else:
                        update_template(int(active["id"]), form_name, form_description, form_system, selected_groups, st.session_state.actor)
                        template_id = int(active["id"])
                        action = "template-update"
                    record(st.session_state.actor, action, "", selected_groups, "Success", form_name.strip())
                    st.session_state.template_mode = "Create"
                    st.session_state.active_template_id = None
                    st.session_state.pop("template_form_selected", None)
                    st.toast("Template saved.")
                    st.rerun()
                except (TemplateValidationError, ValueError) as exc:
                    st.error(str(exc))
            if cancel_col.button("Cancel", use_container_width=False):
                st.session_state.template_mode = "Create"
                st.session_state.active_template_id = None
                st.session_state.pop("template_form_selected", None)
                st.rerun()

    elif mode == "Duplicate" and active:
        with st.expander(f"Duplicate template: {active['name']}", expanded=True):
            duplicate_name = st.text_input("New template name", value=f"{active['name']} Copy", key="duplicate_template_name")
            st.caption(f"The duplicate will contain the same {active['group_count']} groups.")
            d1, d2 = st.columns([1, 4])
            if d1.button("Create duplicate", type="primary"):
                try:
                    new_id = duplicate_template(int(active["id"]), duplicate_name, st.session_state.actor)
                    record(st.session_state.actor, "template-duplicate", "", active["groups"], "Success", duplicate_name.strip())
                    st.session_state.template_mode = "Create"
                    st.session_state.active_template_id = None
                    st.toast("Template duplicated.")
                    st.rerun()
                except (TemplateValidationError, ValueError) as exc:
                    st.error(str(exc))
            if d2.button("Cancel duplicate"):
                st.session_state.template_mode = "Create"
                st.session_state.active_template_id = None
                st.rerun()

    elif mode == "Delete" and active:
        with st.expander(f"Delete template: {active['name']}", expanded=True):
            st.warning("Deleting a template cannot be undone. Adobe users and groups will not be changed.")
            confirmation = st.text_input(f"Type {active['name']} to confirm", key="delete_template_confirmation")
            x1, x2 = st.columns([1, 4])
            if x1.button("Delete permanently", type="primary", disabled=confirmation != active["name"]):
                delete_template(int(active["id"]))
                record(st.session_state.actor, "template-delete", "", active["groups"], "Success", active["name"])
                st.session_state.template_mode = "Create"
                st.session_state.active_template_id = None
                st.toast("Template deleted.")
                st.rerun()
            if x2.button("Cancel delete"):
                st.session_state.template_mode = "Create"
                st.session_state.active_template_id = None
                st.rerun()

elif page == "Audit history":
    df = read(5000)
    if df.empty:
        st.info("No audit records found.")
    else:
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
        st.dataframe(display, use_container_width=True, hide_index=True)
        st.download_button("Download filtered audit CSV", display.to_csv(index=False), "audit-history-filtered.csv", "text/csv")

elif page == "Settings":
    status = catalog_status()
    st.write({
        "mode": "mock" if settings.mock_adobe else "live",
        "writes_enabled": settings.adobe_write_enabled,
        "allowed_domains": sorted(settings.allowed_domains),
        "identity_type": settings.default_identity_type,
        "country": settings.default_country,
        "adobe_configured": settings.adobe_configured,
        "cached_groups": status["group_count"],
        "last_group_sync": status["synced_at"],
    })
    if st.button("Test Adobe connection"):
        try:
            st.success(run(client.test_connection()))
        except Exception as exc:
            st.error(str(exc))
    if st.button("Clear user-directory cache"):
        cached_users.clear()
        st.success("User cache cleared.")
    st.warning("Secrets and write controls remain managed through .env. This version keeps provisioning in Adobe test mode.")
