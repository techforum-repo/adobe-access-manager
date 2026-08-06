import pandas as pd

from adobe_access.users import build_copy_access_preview


def test_copy_preview_marks_existing_and_new_memberships():
    cached = pd.DataFrame([
        {"display_name": "CJA User", "system": "CJA", "adobe_group_name": "BSC-CJA-user"},
        {"display_name": "AEM Author", "system": "AEM", "adobe_group_name": "BSC-AEM-author"},
    ])
    source = {"groups": {"BSC-CJA-user", "BSC-AEM-author"}}
    target_users = [
        {"groups": {"bsc-cja-user"}},
        None,
    ]
    result = build_copy_access_preview(
        source,
        target_users,
        ["one@example.com", "two@example.com"],
        ["BSC-CJA-user", "BSC-AEM-author"],
        cached,
    )
    assert len(result) == 4
    one = result[result["email"] == "one@example.com"]
    assert set(one["membership_status"]) == {"Already assigned", "Will add"}
    two = result[result["email"] == "two@example.com"]
    assert set(two["target_status"]) == {"New user"}
    assert two["will_add"].all()
