import pandas as pd

from adobe_access.client import normalize_user
from adobe_access.users import membership_table


def test_nested_user_object_is_not_used_as_email():
    payload = {
        "user": {
            "username": "albin.issac@bsci.com",
            "firstname": "albin",
            "lastname": "issac",
            "type": "federatedID",
            "groups": ["bsc-cja-admin", "AEM Users - Author"],
        }
    }
    user = normalize_user(payload)
    assert user["email"] == "albin.issac@bsci.com"
    assert user["first_name"] == "albin"
    assert user["last_name"] == "issac"
    assert user["identity_type"] == "federatedID"
    assert user["status"] == "Active"
    assert user["groups"] == {"bsc-cja-admin", "AEM Users - Author"}


def test_custom_memberships_match_cache_case_insensitively():
    user = {"groups": {"bsc-cja-admin", "BSC-CJA-REPORTING-US-DEFAULT"}}
    cache = pd.DataFrame([
        {"adobe_group_name": "BSC-CJA-admin", "display_name": "CJA Admin", "system": "CJA"},
        {"adobe_group_name": "bsc-cja-reporting-us-default", "display_name": "CJA Reporting US", "system": "CJA"},
    ])
    result = membership_table(user, cache)
    assert len(result) == 2
    assert set(result["display_name"]) == {"CJA Admin", "CJA Reporting US"}
