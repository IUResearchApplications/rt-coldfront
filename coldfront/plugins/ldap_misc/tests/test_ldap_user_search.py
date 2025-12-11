from unittest import mock

from django.test import TestCase, override_settings

from coldfront.plugins.ldap_misc.utils.ldap_user_search import get_users_info


@override_settings(LDAP_ENABLE_USER_INFO=True)
class LDAPUserSearchTestCase(TestCase):
    @mock.patch("coldfront.plugins.ldap_misc.utils.ldap_user_search.LDAPUserSearch")
    def test_get_users_info(self, mock_ldap_search):
        usernames = ["john", "doe"]

        # Grab the instance
        mock_ldap_search = mock_ldap_search.return_value

        # Test not finding the users
        mock_ldap_search.search_a_user.side_effect = [[], []]
        users_info = get_users_info(usernames)
        for name, user_info in users_info.items():
            with self.subTest(name=name):
                self.assertEqual(user_info, {})

        # Test finding the users
        mock_ldap_search.search_a_user.side_effect = [[{"username": "john"}], [{"username": "doe"}]]
        users_info = get_users_info(usernames)
        for name, user_info in users_info.items():
            with self.subTest(name=name):
                self.assertEqual(user_info, {"username": name})
