from unittest import mock

from django.test import TestCase, override_settings

from coldfront.plugins.ldap_misc.utils.project import (
    check_current_pi_eligibilities,
    check_if_pi_eligible,
    check_if_pis_eligible,
)


@override_settings(
    LDAP_ENABLE_PROJECT_PI_ELIGIBLE_ADS_GROUPS=True,
    LDAP_PROJECT_PI_ELIGIBLE_ADS_GROUPS=["test-group"],
)
class ProjectTestCase(TestCase):
    @mock.patch("coldfront.plugins.ldap_misc.utils.project.get_user_info")
    def test_check_if_pi_eligible_variants(self, mock_get_user_info):
        username = "john"
        # Test when providing accounts
        self.assertFalse(check_if_pi_eligible(username, []))
        self.assertFalse(check_if_pi_eligible(username, ["other-group"]))
        self.assertTrue(check_if_pi_eligible(username, ["test-group"]))

        # Test without providing accounts
        mock_get_user_info.return_value = {"memberOf": []}
        self.assertFalse(check_if_pi_eligible(username))
        mock_get_user_info.return_value = {"memberOf": ["other-group"]}
        self.assertFalse(check_if_pi_eligible(username))
        mock_get_user_info.return_value = {"memberOf": ["test-group"]}
        self.assertTrue(check_if_pi_eligible(username))

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

    @mock.patch("coldfront.plugins.ldap_misc.utils.project.get_users_info")
    def test_check_current_pi_eligibilities_variants(self, mock_get_users_info):
        usernames = ["john", "doe"]

        def assert_in_helper(usernames, result, assert_in_not_in):
            for name in usernames:
                with self.subTest(name=name):
                    assert_in_not_in(name, result)

        mock_get_users_info.return_value = {"john": {}, "doe": {}}
        result = check_current_pi_eligibilities(usernames)
        assert_in_helper(usernames, result, self.assertIn)

        mock_get_users_info.return_value = {"john": {"memberOf": ["other-group"]}, "doe": {"memberOf": ["other-group"]}}
        result = check_current_pi_eligibilities(usernames)
        assert_in_helper(usernames, result, self.assertIn)

        mock_get_users_info.return_value = {"john": {"memberOf": ["test-group"]}, "doe": {"memberOf": ["test-group"]}}
        result = check_current_pi_eligibilities(usernames)
        assert_in_helper(usernames, result, self.assertNotIn)
