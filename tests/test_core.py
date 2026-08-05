from adobe_access.client import is_user_group
from adobe_access.utils import derive_name, validate_email


def test_name_derivation():
    p = derive_name("john.smith@bsci.com")
    assert p.first_name == "John" and p.last_name == "Smith"


def test_domain_validation():
    assert validate_email("john.smith@bsci.com", {"bsci.com"})[0]
    assert not validate_email("john.smith@gmail.com", {"bsci.com"})[0]


def test_user_group_filter():
    assert is_user_group({"type": "user-group", "groupName": "AEM Authors"})
    assert is_user_group({"groupType": "user_group", "groupName": "CJA Analysts"})
    assert not is_user_group({"type": "product-profile", "groupName": "AEM Profile"})
    assert not is_user_group({"type": "group", "groupName": "Ambiguous Group"})
    assert not is_user_group({"groupName": "Missing Type"})
