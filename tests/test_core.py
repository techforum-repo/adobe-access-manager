from adobe_access.client import is_user_group
from adobe_access.utils import derive_name, validate_email


def test_name_derivation():
    p = derive_name("john.smith@example.com")
    assert p.first_name == "John" and p.last_name == "Smith"


def test_domain_validation():
    assert validate_email("john.smith@example.com", {"example.com"})[0]
    assert not validate_email("john.smith@gmail.com", {"example.com"})[0]


def test_firstname_lastname_format_is_required():
    valid, note = validate_email("john.smith@example.com", {"example.com"})
    assert valid
    assert note == ""


def test_a_single_word_local_part_is_rejected():
    valid, note = validate_email("jsmith@example.com", {"example.com"})
    assert not valid
    assert "firstname.lastname" in note


def test_underscore_or_hyphen_separated_local_parts_are_rejected():
    assert not validate_email("john_smith@example.com", {"example.com"})[0]
    assert not validate_email("john-smith@example.com", {"example.com"})[0]


def test_a_digit_in_the_local_part_is_rejected():
    assert not validate_email("john.smith2@example.com", {"example.com"})[0]


def test_three_or_more_dot_separated_parts_are_rejected():
    assert not validate_email("john.q.smith@example.com", {"example.com"})[0]


def test_firstname_lastname_check_is_case_insensitive():
    assert validate_email("John.Smith@example.com", {"example.com"})[0]


def test_user_group_filter():
    assert is_user_group({"type": "user-group", "groupName": "AEM Authors"})
    assert is_user_group({"groupType": "user_group", "groupName": "CJA Analysts"})
    assert not is_user_group({"type": "product-profile", "groupName": "AEM Profile"})
    assert not is_user_group({"type": "group", "groupName": "Ambiguous Group"})
    assert not is_user_group({"groupName": "Missing Type"})
