from unittest import mock

from django.test import TestCase

from coldfront.plugins.ldap_misc.utils.ldap_user_search import get_user_info, get_users_info


class LDAPUserSearchTestCase(TestCase):
    @mock.patch("coldfront.plugins.ldap_misc.utils.ldap_user_search.LDAPUserSearch")
    def test_get_user_info(self, mock_ldap_search):
        username = "john"

        # Test when no user is found
        mock_ldap_search.attribute = "search_a_user"
        mock_ldap_search.search_a_user.return_value = []
        user_info = get_user_info(username, mock_ldap_search)
        mock_ldap_search.search_a_user.assert_called_once_with(username, "username_only")
        self.assertEqual(user_info, {})
        mock_ldap_search.search_a_user.reset_mock()

        # Test when a user is found
        mock_ldap_search.search_a_user.return_value = [{"username": "john"}]
        user_info = get_user_info(username, mock_ldap_search)
        mock_ldap_search.search_a_user.assert_called_once_with(username, "username_only")
        self.assertEqual(user_info, {"username": "john"})

    @mock.patch("coldfront.plugins.ldap_misc.utils.ldap_user_search.LDAPUserSearch")
    @mock.patch("coldfront.plugins.ldap_misc.utils.ldap_user_search.get_user_info")
    def test_get_users_info(self, mock_get_user_info, mock_ldap_search):
        usernames = ["john", "doe"]

        # Mock LDAP user search since we tested it earlier
        mock_ldap_search.return_value = mock.Mock()

        # Test not finding the users
        mock_get_user_info.side_effect = [{}, {}]
        users_info = get_users_info(usernames)
        for name in usernames:
            with self.subTest(name=name):
                self.assertEqual(users_info.get(name), {})

        # Test finding the users
        mock_get_user_info.side_effect = [{"username": "john"}, {"username": "doe"}]
        users_info = get_users_info(usernames)
        for name in usernames:
            with self.subTest(name=name):
                self.assertEqual(users_info.get(name), {"username": name})
