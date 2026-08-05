import pandas as pd

from adobe_access.users import compare_custom_group_memberships


def test_compare_filters_to_cached_custom_groups():
    cached = pd.DataFrame([
        {"adobe_group_name": "BSC-CJA-A", "display_name": "CJA A", "system": "CJA"},
        {"adobe_group_name": "BSC-AEM-B", "display_name": "AEM B", "system": "AEM"},
        {"adobe_group_name": "BSC-AEP-C", "display_name": "AEP C", "system": "AEP"},
    ])
    left = {"groups": {"bsc-cja-a", "BSC-AEM-B", "SYSTEM-GROUP"}}
    right = {"groups": {"BSC-CJA-A", "BSC-AEP-C"}}
    result = compare_custom_group_memberships(left, right, cached)
    assert set(result["adobe_group_name"]) == {"BSC-CJA-A", "BSC-AEM-B", "BSC-AEP-C"}
    values = dict(zip(result["adobe_group_name"], result["comparison"]))
    assert values["BSC-CJA-A"] == "Shared"
    assert values["BSC-AEM-B"] == "Only first user"
    assert values["BSC-AEP-C"] == "Only second user"


def test_compare_returns_empty_when_no_cached_memberships():
    cached = pd.DataFrame([{"adobe_group_name": "BSC-CJA-A", "display_name": "CJA A", "system": "CJA"}])
    result = compare_custom_group_memberships({"groups": {"SYSTEM-A"}}, {"groups": {"SYSTEM-B"}}, cached)
    assert result.empty
