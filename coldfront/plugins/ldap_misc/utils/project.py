from django.conf import settings

from coldfront.plugins.ldap_misc.utils.ldap_user_search import get_user_info, get_users_info


def check_if_pi_eligible(username: str, memberships=None) -> bool:
    if not settings.LDAP_ENABLE_PROJECT_PI_ELIGIBLE_ADS_GROUPS:
        return True

    if not memberships:
        memberships = get_user_info(username).get("memberOf")

    if not memberships:
        return False

    for membership in memberships:
        if membership in settings.LDAP_PROJECT_PI_ELIGIBLE_ADS_GROUPS:
            return True

    return False


def check_if_pis_eligible(usernames: list[str]) -> dict:
    if not settings.LDAP_ENABLE_PROJECT_PI_ELIGIBLE_ADS_GROUPS:
        return {}

    eligible_statuses = {}
    users_info = get_users_info(usernames)
    for username, user_info in users_info.items():
        for user_membersip in user_info.get("memberOf", []):
            eligible = user_membersip in settings.LDAP_PROJECT_PI_ELIGIBLE_ADS_GROUPS
            eligible_statuses[username] = eligible
            if eligible:
                break

    return eligible_statuses


def check_current_pi_eligibilities(project_usernames: list[str]) -> list[str]:
    users_info = get_users_info(project_usernames)

    ineligible_pis = []
    for username, user_info in users_info.items():
        if not check_if_pi_eligible(username, user_info.get("memberOf", [])):
            ineligible_pis.append(username)

    return ineligible_pis
