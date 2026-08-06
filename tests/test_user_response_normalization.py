from adobe_access.client import normalize_user
from adobe_access.users import membership_table
import pandas as pd


def test_normalize_nested_exact_user_response():
    payload = {
        "user": {
            "username": "Jane.Doe@example.com",
            "firstname": "Jane",
            "lastname": "Doe",
            "type": "federatedID",
            "status": "active",
            "groups": [
                {"groupName": "BSC-CJA-QA"},
                {"name": "SYSTEM-PROFILE"},
            ],
        }
    }
    user = normalize_user(payload)
    assert user["email"] == "jane.doe@example.com"
    assert user["first_name"] == "Jane"
    assert user["identity_type"] == "federatedID"
    assert user["groups"] == {"BSC-CJA-QA", "SYSTEM-PROFILE"}


def test_membership_table_only_returns_cached_custom_groups():
    user = {"groups": {"BSC-CJA-QA", "SYSTEM-PROFILE"}}
    cached = pd.DataFrame([
        {"adobe_group_name": "BSC-CJA-QA", "display_name": "CJA QA", "system": "CJA"}
    ])
    result = membership_table(user, cached)
    assert result["adobe_group_name"].tolist() == ["BSC-CJA-QA"]
    assert result.attrs["ignored_non_custom_memberships"] == 1
