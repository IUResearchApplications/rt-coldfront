from coldfront.plugins.ldap_user_search.utils import LDAPUserSearch


def get_user_info(username: str, ldap_search: LDAPUserSearch = None) -> dict:
    """
    Runs an LDAP query to find the info specified in the ENV variable LDAP_USER_SEARCH_ATTRIBUTE_MAP. If the user is not
    found an empty dictionary is returned.
    
    :param username: The username to search for
    :param ldap_search: An instance of the LDAPUserSearch class used to search for the user
    """
    if ldap_search is None:
        ldap_search = LDAPUserSearch(None, None)
    user_info = ldap_search.search_a_user(username, "username_only")
    if not user_info:
        return {}
    return user_info[0]


def get_users_info(usernames: list[str]) -> dict:
    """
    Runs an LDAP query for each username to find the info specified in the ENV variable LDAP_USER_SEARCH_ATTRIBUTE_MAP.
    Returns a dictionary with the key as the username and the value as the results found for that user.
    
    :param usernames: List of usernames to search for
    """
    ldap_search = LDAPUserSearch(None, None)
    results = {}
    for username in usernames:
        results[username] = get_user_info(username, ldap_search)

    return results
