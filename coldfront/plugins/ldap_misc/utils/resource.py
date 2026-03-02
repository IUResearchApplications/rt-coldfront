from django.conf import settings

from coldfront.plugins.ldap_misc.utils.ldap_user_search import get_users_info


def get_users_accounts(usernames: list[str]) -> dict | None:
    """Finds the accounts the users have with LDAP.

    Params:
        usernames (list): a list of usernames

    Returns:
        dict | None: a dictionary with the users' accounts or None if LDAP_ENABLE_RESOURCE_ACCOUNT_CHECKING is False
    """
    if not settings.LDAP_ENABLE_RESOURCE_ACCOUNT_CHECKING:
        return None

    results = {}
    users_info = get_users_info(usernames)
    for username, user_info in users_info.items():
        if user_info is None:
            results[username] = []
        else:
            results[username] = user_info.get("memberOf", [])

    return results


def get_user_account_statuses(
    usernames: list[str], resource: str | None, all_user_accounts: dict | None = None
) -> dict:
    """Checks if an account exists for the resource by comparing the provided list

    Params:
        accounts: A list of accounts to check against

    Returns:
        dict(exists: bool - If the require account exists, reason: str - Why the account was found/not found)
    """
    if resource is None:
        return dict.fromkeys(usernames, {"exists": True, "reason": "not_required"})

    if all_user_accounts is None:
        all_user_accounts = get_users_accounts(usernames)
    if all_user_accounts is None:
        return dict.fromkeys(usernames, {"exists": True, "reason": "not_enabled"})

    resource_acc = settings.LDAP_RESOURCE_ACCOUNTS.get(resource)
    results = {}
    for username, user_accounts in all_user_accounts.items():
        if not user_accounts:
            results[username] = {"exists": False, "reason": "no_account"}
        elif not resource_acc:
            results[username] = {"exists": True, "reason": "has_account"}
        elif resource_acc in user_accounts:
            results[username] = {"exists": True, "reason": "has_resource_account"}
        else:
            results[username] = {"exists": False, "reason": "no_resource_account"}

    return results
