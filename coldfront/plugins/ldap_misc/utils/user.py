from coldfront.plugins.ldap_misc.utils.ldap_user_search import get_users_info


def get_users_accounts(usernames: str) -> dict:
    selected_users_accounts = dict.fromkeys(usernames, [])
    users_info = get_users_info(usernames)
    for username, user_info in users_info.items():
        selected_users_accounts[username] = user_info.get("memberOf")

    return selected_users_accounts
