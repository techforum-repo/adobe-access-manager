from __future__ import annotations

import pandas as pd

from adobe_access.users import membership_table, user_export_table


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


def test_user_export_contains_only_user_and_custom_group_columns():
    user = {
        "email": "albin.issac@bsci.com",
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
