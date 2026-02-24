from django.conf import settings

from coldfront.plugins.ldap_user_search.utils import LDAPUserSearch


def get_users_info(usernames: list[str]) -> dict:
    """Runs an LDAP query for each username to find the info specified in the ENV variable
    LDAP_USER_SEARCH_ATTRIBUTE_MAP.

    Params:
        usernames (list): a list of usernames to search for
        
    Returns:
        dict: a dictionary info found for each user, or each users' info is None if LDAP_ENABLE_USER_INFO is False
    """
    if not settings.LDAP_ENABLE_USER_INFO:
        return dict.fromkeys(usernames, None)

    ldap_search = LDAPUserSearch(None, None)
    results = {}
    for username in usernames:
        user_info = ldap_search.search_a_user(username, "username_only")
        if not user_info:
            results[username] = {}
        else:
            results[username] = user_info[0]

    return results
