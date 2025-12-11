from django.conf import settings

from coldfront.plugins.ldap_user_search.utils import LDAPUserSearch


def get_users_info(usernames: list[str]) -> dict:
    """
    Runs an LDAP query for each username to find the info specified in the ENV variable LDAP_USER_SEARCH_ATTRIBUTE_MAP.
    Returns a dictionary with the key as the username and the value as the results found for that user. If
    LDAP_ENABLE_USER_INFO is False then the value for each user entry is None.

    :param usernames: List of usernames to search for
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
