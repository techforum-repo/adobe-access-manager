from __future__ import annotations

import pandas as pd

from adobe_access.users import compare_special_permissions, membership_table, special_permissions, user_export_table


def test_membership_table_returns_only_cached_custom_groups_case_insensitively():
    user = {"groups": {"BSC-CJA-Users", "_product_admin_internal"}}
    cache = pd.DataFrame([
        {
            "adobe_group_name": "bsc-cja-users",
            "display_name": "CJA Users",
            "system": "CJA",
            "description": "",
            "privileged": False,
            "member_count": 1,
        }
    ])
    result = membership_table(user, cache)
    assert result["adobe_group_name"].tolist() == ["bsc-cja-users"]
    assert result["display_name"].tolist() == ["CJA Users"]


def test_membership_table_excludes_special_permissions_from_the_ignored_count():
    """A special-permission entry (like the fake _product_admin_internal above)
    must not inflate "ignored_non_custom_memberships" — that count means "a
    real custom group that just isn't synced yet", not an admin role, which
    has its own dedicated section (special_permissions()) instead."""
    user = {"groups": {"_org_admin", "SOME-UNSYNCED-GROUP"}}
    result = membership_table(user, pd.DataFrame(columns=["adobe_group_name"]))
    assert result.attrs["ignored_non_custom_memberships"] == 1, (
        "only the real unsynced group should count as ignored, not the admin role"
    )


def test_special_permissions_extracts_admin_roles_with_friendly_labels():
    user = {"groups": {"_org_admin", "AEM-PROD-AUTHORS", "_support_admin"}}
    result = special_permissions(user)
    assert set(result["raw"]) == {"_org_admin", "_support_admin"}
    assert set(result["category"]) == {"System Administrator", "Support Administrator"}


def test_special_permissions_gives_each_product_admin_entry_its_own_row_with_detail():
    """One row per role instance — a user with Product Administrator on two
    products gets two rows sharing the same category, different detail."""
    user = {"groups": {"_product_admin_target", "_product_admin_aem"}}
    result = special_permissions(user)
    assert len(result) == 2
    assert set(result["category"]) == {"Product Administrator"}
    assert set(result["detail"]) == {"target", "aem"}


def test_special_permissions_is_empty_for_a_user_with_no_admin_roles():
    user = {"groups": {"AEM-PROD-AUTHORS", "CJA-ANALYSTS"}}
    assert special_permissions(user).empty


def test_compare_special_permissions_marks_shared_and_exclusive_roles():
    left = {"groups": {"_org_admin", "_support_admin"}}
    right = {"groups": {"_org_admin"}}
    result = compare_special_permissions(left, right)
    shared = result[result["raw"] == "_org_admin"].iloc[0]
    assert shared["comparison"] == "Shared"
    only_left = result[result["raw"] == "_support_admin"].iloc[0]
    assert only_left["comparison"] == "Only first user"


def test_compare_special_permissions_is_empty_when_neither_user_has_any():
    left = {"groups": {"AEM-PROD-AUTHORS"}}
    right = {"groups": {"CJA-ANALYSTS"}}
    assert compare_special_permissions(left, right).empty


def test_user_export_contains_only_user_and_custom_group_columns():
    user = {
        "email": "albin.issac@example.com",
        "first_name": "Albin",
        "last_name": "Issac",
        "identity_type": "federatedID",
        "status": "Active",
    }
    memberships = pd.DataFrame([
        {
            "display_name": "CJA Users",
            "system": "CJA",
            "adobe_group_name": "bsc-cja-users",
            "cached": True,
        }
    ])
    result = user_export_table(user, memberships)
    assert result.columns.tolist() == [
        "email", "first_name", "last_name", "identity_type", "status",
        "group_display_name", "system", "adobe_group_name",
    ]
    assert result.iloc[0]["adobe_group_name"] == "bsc-cja-users"
