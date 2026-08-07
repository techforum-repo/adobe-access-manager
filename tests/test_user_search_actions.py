from __future__ import annotations

import pandas as pd
import pytest

from adobe_access import database
from adobe_access.users import compare_special_permissions, membership_table, special_permissions, user_export_table


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    # special_permissions()/compare_special_permissions() now read the synced
    # group cache (to disambiguate Adobe's ambiguous "_admin_<name>" — see
    # utils.py's classify_special_permission()), so every test in this file
    # needs a real, initialized DB even where it isn't the focus.
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "user_search_actions.db")
    database.initialize()
    return database.DB_PATH


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


def test_special_permissions_disambiguates_admin_using_the_synced_group_cache():
    """Confirmed against real tenant data AND Adobe's own UMAPI documentation:
    "_admin_<name>" is used for BOTH Profile Administrator and User Group
    Administrator, with nothing in the string itself distinguishing them —
    Adobe's docs say resolving it requires checking <name> against a real
    list of profiles/groups. The only such list this app has is its own
    synced custom-group cache: a matching group name means User Group
    Administrator for that group; no match means Profile Administrator
    (product profiles are never synced separately to check against)."""
    database.replace_managed_groups([
        {"name": "sample-user-group", "system": "Other"},
    ])
    user = {"groups": {
        "_admin_SampleProfile",  # not a synced group -> Profile Administrator
        "_admin_sample-user-group",  # is a synced group -> User Group Administrator
        "_admin_SAMPLE-USER-GROUP",  # same group, different case -> still matches
    }}
    result = special_permissions(user)
    by_raw = dict(zip(result["raw"], result["category"]))
    assert by_raw["_admin_SampleProfile"] == "Profile Administrator"
    assert by_raw["_admin_sample-user-group"] == "User Group Administrator"
    assert by_raw["_admin_SAMPLE-USER-GROUP"] == "User Group Administrator"


def test_special_permissions_admin_falls_back_to_profile_administrator_without_a_synced_cache():
    user = {"groups": {"_admin_sample-user-group"}}
    result = special_permissions(user)
    assert result.iloc[0]["category"] == "Profile Administrator"


def test_special_permissions_disambiguates_admin_when_the_group_name_has_a_space():
    """Adobe's raw "_admin_<name>" slug substitutes an underscore for every
    space in the group name (e.g. a group named "Sample User Group" becomes
    "_admin_Sample_User_Group") — but the synced group cache stores the name
    exactly as Adobe's Groups API returned it, which is not guaranteed to use
    a space there either. Whichever side ends up using an underscore where
    the other uses a space, the two must still be recognized as the same
    group."""
    database.replace_managed_groups([
        # The cache's own copy of the name uses an underscore where the raw
        # admin slug conceptually has a word-separator too — a real-world
        # shape this app has actually seen from Adobe's Groups API.
        {"name": "Sample_User_Group", "system": "Other"},
    ])
    user = {"groups": {
        "_admin_Sample_User_Group",  # raw slug: underscore, cache: underscore
    }}
    result = special_permissions(user)
    assert result.iloc[0]["category"] == "User Group Administrator"


def test_special_permissions_disambiguates_admin_when_the_cache_name_has_a_space():
    """The mirror image of the case above: the cache stores the name with a
    literal space, and the raw admin slug (as Adobe always encodes it)
    substitutes an underscore for that space."""
    database.replace_managed_groups([
        {"name": "Sample User Group", "system": "Other"},
    ])
    user = {"groups": {"_admin_Sample_User_Group"}}
    result = special_permissions(user)
    assert result.iloc[0]["category"] == "User Group Administrator"


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
