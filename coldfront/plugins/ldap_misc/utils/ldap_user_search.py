from coldfront.plugins.ldap_user_search.utils import LDAPUserSearch


def get_user_info(username: str, ldap_search: LDAPUserSearch = None) -> dict:
    if ldap_search is None:
        ldap_search = LDAPUserSearch(None, None)
    user_info = ldap_search.search_a_user(username, "username_only")
    if not user_info:
        return {}
    return user_info[0]


def get_users_info(usernames: list[str]) -> dict:
    ldap_search = LDAPUserSearch(None, None)
    results = {}
    for username in usernames:
        results[username] = get_user_info(username, ldap_search)

    return results
