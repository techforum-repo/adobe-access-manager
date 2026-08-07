from adobe_access.client import is_user_group
from adobe_access.utils import (
    classify_special_permission,
    derive_name,
    describe_special_permission,
    is_special_permission,
    validate_email,
)


def test_name_derivation():
    p = derive_name("john.smith@example.com")
    assert p.first_name == "John" and p.last_name == "Smith"


def test_name_derivation_strips_a_trailing_disambiguation_digit():
    """"john2.doe" is a common pattern for "john.doe" already being taken —
    Adobe should get "John" as the first name, not the literal "John2"."""
    p = derive_name("john2.doe3@example.com")
    assert p.first_name == "John" and p.last_name == "Doe"
    assert p.ambiguous is False


def test_name_derivation_falls_back_to_the_original_part_when_purely_numeric():
    p = derive_name("123.doe@example.com")
    assert p.first_name == "123" and p.last_name == "Doe"


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


def test_a_trailing_disambiguation_digit_is_allowed():
    """e.g. "john.smith2" when "john.smith" is already taken."""
    assert validate_email("john.smith2@example.com", {"example.com"})[0]
    assert validate_email("john2.smith@example.com", {"example.com"})[0]
    assert validate_email("john2.smith3@example.com", {"example.com"})[0]


def test_a_leading_or_embedded_digit_is_still_rejected():
    assert not validate_email("2john.smith@example.com", {"example.com"})[0]
    assert not validate_email("jo2hn.smith@example.com", {"example.com"})[0]


def test_is_special_permission_detects_the_underscore_prefix():
    assert is_special_permission("_org_admin")
    assert is_special_permission("_admin_12345")
    assert not is_special_permission("AEM-PROD-AUTHORS")
    assert not is_special_permission("BSC-CJA-USERS")


def test_is_special_permission_also_detects_human_readable_role_phrases():
    """Confirmed against real tenant data — the same role type isn't always
    returned with the underscore-prefixed slug."""
    assert is_special_permission("Product Administrator")
    assert is_special_permission("Product Administrator - Adobe Target")
    assert is_special_permission("Profile Administrator - Target Default")
    assert is_special_permission("_developer")


def test_is_special_permission_does_not_misfire_on_a_real_custom_group():
    """A non-underscore-prefixed name must match the FULL word "Administrator",
    not bare "Admin" — otherwise a real custom group like "Product Admin
    Access Group" would be wrongly excluded from the custom-group cache and
    miscategorized as an Adobe admin role."""
    assert not is_special_permission("Product Admin Access Group")
    assert not is_special_permission("Product Administration Team")


def test_developer_prefix_groups_together_with_any_suffix():
    """Confirmed against real tenant data: any "_developer..." name (not just
    the bare "_developer") groups under the same Developer category."""
    assert describe_special_permission("_developer") == "Developer"
    assert describe_special_permission("_developer_12345") == "Developer (12345)"


def test_unqualified_admin_prefix_groups_as_profile_administrator():
    """Confirmed against real tenant data: in this tenant, "_admin..." with no
    "product"/"profile" in the name is how Profile Administrator is
    represented — distinct from the "_product_admin..." form."""
    assert describe_special_permission("_admin") == "Profile Administrator"
    assert describe_special_permission("_admin_default") == "Profile Administrator (default)"


def test_product_admin_stays_distinct_from_the_generic_admin_catch_all():
    """"_product_admin..." doesn't literally start with "_admin" (it starts
    with "_product"), so the generic "_admin" catch-all must never absorb it
    into an undifferentiated Profile Administrator bucket."""
    assert describe_special_permission("_product_admin_target") == "Product Administrator (target)"


def test_product_admin_detail_drops_the_redundant_slug_repeat_and_env_suffix():
    """Based on a real-shaped example: Adobe product profile names repeat the
    readable profile name as a lowercase slug immediately after itself, then
    tack on a technical environment/instance suffix — keep just the product
    and the readable profile name."""
    raw = (
        "Product Administrator - Adobe Experience Manager as a Cloud Service - "
        "Example Web Platform-example-web-platform-prod-publish"
    )
    result = classify_special_permission(raw)
    assert result.category == "Product Administrator"
    assert result.detail == "Adobe Experience Manager as a Cloud Service - Example Web Platform"


def test_product_admin_detail_without_a_redundant_slug_is_left_unchanged():
    """No self-repeat present — must not truncate or otherwise mangle it."""
    assert describe_special_permission("Product Administrator - Adobe Target") == "Product Administrator (Adobe Target)"


def test_slug_dedup_only_applies_within_the_profile_segment_not_the_product_name():
    """The product-name portion (before the last " - ") must never be searched
    for a self-repeat — only the profile-name segment after it can have one."""
    raw = "Product Administrator - Customer Journey Analytics - Reporting Profile-reporting-profile-prod"
    result = classify_special_permission(raw)
    assert result.detail == "Customer Journey Analytics - Reporting Profile"


def test_user_group_administrator_recognized_only_in_its_confirmed_human_readable_form():
    """Per Adobe's own UMAPI documentation, there is no distinct underscore
    slug for User Group Administrator — it shares the exact same "_admin_<name>"
    prefix as Profile Administrator (see the module docstring on
    classify_special_permission for why, and test_user_search_actions.py for
    how that ambiguous case gets resolved via the synced group cache). Only
    the literal human-readable phrase is unambiguous on its own."""
    assert describe_special_permission("User Group Administrator") == "User Group Administrator"
    assert describe_special_permission("User Group Administrator - Example Group") == "User Group Administrator (Example Group)"
    # A guessed underscore slug with no cache to check against must NOT be
    # hardcoded to this category — it falls into the same ambiguous "_admin_"
    # handling as Profile Administrator, defaulting there without a match.
    assert describe_special_permission("_admin_user_group") == "Profile Administrator (user group)"


def test_describe_special_permission_uses_known_friendly_labels():
    assert describe_special_permission("_org_admin") == "System Administrator"
    assert describe_special_permission("_support_admin") == "Support Administrator"
    assert describe_special_permission("_deployment_admin") == "Deployment Administrator"


def test_describe_special_permission_handles_product_admin_with_a_dynamic_suffix():
    """Confirmed against real tenant data: the raw name isn't a single fixed
    string — observed both an underscore slug and a human-readable phrase."""
    assert describe_special_permission("_product_admin_target") == "Product Administrator (target)"
    assert describe_special_permission("_product_admin") == "Product Administrator"
    assert describe_special_permission("Product Administrator - Adobe Target") == "Product Administrator (Adobe Target)"
    assert describe_special_permission("Product Administrator") == "Product Administrator"


def test_describe_special_permission_falls_back_to_the_raw_name_when_unknown():
    """Never invent a label it isn't sure of — an unrecognized underscore-prefixed
    role still surfaces under its real Adobe name rather than a guess."""
    assert describe_special_permission("_some_future_admin_role") == "_some_future_admin_role"


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
