from unittest import mock

from django.test import TestCase, override_settings

from coldfront.plugins.ldap_misc.utils.resource import get_user_account_statuses, get_users_accounts


@override_settings(
    LDAP_ENABLE_RESOURCE_ACCOUNT_CHECKING=True,
    LDAP_RESOURCE_ACCOUNTS={"test_resource": "test1"},
)
class ResourceTestCase(TestCase):
    @mock.patch("coldfront.plugins.ldap_misc.utils.resource.get_users_info")
    def test_get_users_accounts(self, mock_get_users_info):
        usernames = ["john", "doe"]
        mock_get_users_info.return_value = {"john": {"memberOf": ["test1"]}, "doe": {"memberOf": ["test1"]}}
        user_accounts = get_users_accounts(usernames)
        for name, accounts in user_accounts.items():
            with self.subTest(name=name):
                self.assertEqual(accounts, ["test1"])

    @mock.patch("coldfront.plugins.ldap_misc.utils.resource.get_users_accounts")
    def test_get_user_account_statuses_variants(self, mock_get_users_accounts):
        def helper(user_account_statuses, assert_bool, reason):
            for name, status in user_account_statuses.items():
                with self.subTest(name=name):
                    assert_bool(status.get("exists"))
                    self.assertEqual(status.get("reason"), reason)

        usernames = ["john", "doe"]
        user_account_statuses = get_user_account_statuses(usernames, None)
        helper(user_account_statuses, self.assertTrue, "not_required")

        # First, we run tests with provided user_accounts
        # No accounts
        user_accounts = {"john": [], "doe": []}
        user_account_statuses = get_user_account_statuses(usernames, "test_resource", user_accounts)
        helper(user_account_statuses, self.assertFalse, "no_account")

        # Missing resource accounts
        user_accounts = {"john": ["wrong_account"], "doe": ["wrong_account"]}
        user_account_statuses = get_user_account_statuses(usernames, "test_resource", user_accounts)
        helper(user_account_statuses, self.assertFalse, "no_resource_account")

        # Resource only requires an IU account
        user_accounts = {"john": ["waccount"], "doe": ["account"]}
        user_account_statuses = get_user_account_statuses(usernames, "test_resource2", user_accounts)
        helper(user_account_statuses, self.assertTrue, "has_account")

        # Resource requires an account
        user_accounts = {"john": ["test1"], "doe": ["test1"]}
        user_account_statuses = get_user_account_statuses(usernames, "test_resource", user_accounts)
        helper(user_account_statuses, self.assertTrue, "has_resource_account")

        # Now, we will run tests without providing accounts
        # Disabled
        mock_get_users_accounts.return_value = None
        user_account_statuses = get_user_account_statuses(usernames, "test_resource")
        helper(user_account_statuses, self.assertTrue, "not_enabled")

        # # No accounts
        mock_get_users_accounts.return_value = {"john": [], "doe": []}
        user_account_statuses = get_user_account_statuses(usernames, "test_resource")
        helper(user_account_statuses, self.assertFalse, "no_account")

        # Missing resource accounts
        mock_get_users_accounts.return_value = {"john": ["wrong_account"], "doe": ["wrong_account"]}
        user_account_statuses = get_user_account_statuses(usernames, "test_resource")
        helper(user_account_statuses, self.assertFalse, "no_resource_account")

        # Resource only requires an IU account
        mock_get_users_accounts.return_value = {"john": ["waccount"], "doe": ["account"]}
        user_account_statuses = get_user_account_statuses(usernames, "test_resource2")
        helper(user_account_statuses, self.assertTrue, "has_account")

        # Resource requires an account
        mock_get_users_accounts.return_value = {"john": ["test1"], "doe": ["test1"]}
        user_account_statuses = get_user_account_statuses(usernames, "test_resource")
        helper(user_account_statuses, self.assertTrue, "has_resource_account")
