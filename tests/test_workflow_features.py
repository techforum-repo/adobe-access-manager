from adobe_access.provisioning import compare_users


def test_compare_users_marks_differences():
    result = compare_users({"groups": {"A", "B"}}, {"groups": {"B", "C"}})
    assert set(result[result["difference"] == "Different"]["group"]) == {"A", "C"}
    assert set(result[result["difference"] == "Same"]["group"]) == {"B"}
