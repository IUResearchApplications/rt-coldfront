from unittest import mock

from django.test import TestCase, override_settings

from coldfront.plugins.ldap_misc.utils.project import (
    check_if_pis_eligible,
    get_ineligible_pis,
)


@override_settings(
    LDAP_ENABLE_PROJECT_PI_ELIGIBLE_ADS_GROUPS=True,
    LDAP_PROJECT_PI_ELIGIBLE_ADS_GROUPS=["test-group"],
)
class ProjectTestCase(TestCase):
    @mock.patch("coldfront.plugins.ldap_misc.utils.project.get_users_info")
    def test_check_if_pis_eligible_variants(self, mock_get_users_info):
        usernames = ["john", "doe"]

        def assert_bool_helper(usernames, result, assert_bool):
            for name in usernames:
                with self.subTest(name=name):
                    assert_bool(result.get(name))

        mock_get_users_info.return_value = {"john": {}, "doe": {}}
        result = check_if_pis_eligible(usernames)
        assert_bool_helper(usernames, result, self.assertFalse)

        mock_get_users_info.return_value = {"john": {"memberOf": ["other-group"]}, "doe": {"memberOf": ["other-group"]}}
        result = check_if_pis_eligible(usernames)
        assert_bool_helper(usernames, result, self.assertFalse)

        mock_get_users_info.return_value = {"john": {"memberOf": ["test-group"]}, "doe": {"memberOf": ["test-group"]}}
        result = check_if_pis_eligible(usernames)
        assert_bool_helper(usernames, result, self.assertTrue)

    @mock.patch("coldfront.plugins.ldap_misc.utils.project.check_if_pis_eligible")
    def test_get_ineligible_pis_variants(self, mock_check_if_pis_eligible):
        usernames = ["john", "doe"]

        def assert_in_not_in_helper(usernames, result, assert_in_not_in):
            for name in usernames:
                with self.subTest(name=name):
                    assert_in_not_in(name, result)

        mock_check_if_pis_eligible.return_value = {"john": False, "doe": False}
        result = get_ineligible_pis(usernames)
        assert_in_not_in_helper(usernames, result, self.assertIn)

        mock_check_if_pis_eligible.return_value = {"john": True, "doe": True}
        result = get_ineligible_pis(usernames)
        assert_in_not_in_helper(usernames, result, self.assertNotIn)
