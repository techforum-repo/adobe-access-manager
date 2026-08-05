from __future__ import annotations

import pandas as pd
import pytest

from adobe_access.users import UserLookupError, membership_table, normalize_lookup_email, user_export_table


def test_normalize_lookup_email() -> None:
    assert normalize_lookup_email("  Person.Name@BSCI.COM ") == "person.name@bsci.com"
    with pytest.raises(UserLookupError):
        normalize_lookup_email("person")


def test_membership_table_enriches_cached_groups() -> None:
    cached = pd.DataFrame([
        {
            "adobe_group_name": "BSC-CJA-reporting-US-default",
            "display_name": "CJA Reporting US Default",
            "system": "CJA",
        }
    ])
    user = {"groups": {"BSC-CJA-reporting-US-default", "Uncached Group"}}
    result = membership_table(user, cached)
    assert len(result) == 1
    cached_row = result[result["adobe_group_name"] == "BSC-CJA-reporting-US-default"].iloc[0]
    assert cached_row["display_name"] == "CJA Reporting US Default"
    assert bool(cached_row["cached"]) is True


def test_user_export_table_is_flat() -> None:
    user = {
        "email": "person@bsci.com",
        "first_name": "Person",
        "last_name": "Example",
        "identity_type": "federatedID",
        "status": "active",
    }
    memberships = pd.DataFrame([
        {"display_name": "CJA User", "system": "CJA", "adobe_group_name": "BSC-CJA-user", "cached": True}
    ])
    result = user_export_table(user, memberships)
    assert result.loc[0, "email"] == "person@bsci.com"
    assert result.loc[0, "adobe_group_name"] == "BSC-CJA-user"
