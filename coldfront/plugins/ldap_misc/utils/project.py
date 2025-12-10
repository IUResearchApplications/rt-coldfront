from django.conf import settings

from coldfront.plugins.ldap_misc.utils.ldap_user_search import get_user_info, get_users_info


def check_if_pi_eligible(project_pi_username: str, project_pi_memberships: list = None) -> bool:
    """
    Returns if a project PI is still eligible to be a PI.

    :param project_pi_username: The PI's username
    :param project_pi_memberships: The PI's list of memberships
    """
    if not settings.LDAP_ENABLE_PROJECT_PI_ELIGIBLE_ADS_GROUPS:
        return True

    if not project_pi_memberships:
        project_pi_memberships = get_user_info(project_pi_username).get("memberOf")

    if not project_pi_memberships:
        return False

    for membership in project_pi_memberships:
        if membership in settings.LDAP_PROJECT_PI_ELIGIBLE_ADS_GROUPS:
            return True

    return False


def check_if_pis_eligible(project_pi_usernames: list[str]) -> dict:
    """
    Returns a dictionary of project PIs with their usernames as the key and their eligibility to be a PI as the value.

    :param project_pi_usernames: A list of project PI usernames
    """
    if not settings.LDAP_ENABLE_PROJECT_PI_ELIGIBLE_ADS_GROUPS:
        return {}

    eligible_statuses = {}
    users_info = get_users_info(project_pi_usernames)
    for username, user_info in users_info.items():
        for user_membersip in user_info.get("memberOf", []):
            eligible = user_membersip in settings.LDAP_PROJECT_PI_ELIGIBLE_ADS_GROUPS
            eligible_statuses[username] = eligible
            if eligible:
                break

    return eligible_statuses


def check_current_pi_eligibilities(project_pi_usernames: list[str]) -> list[str]:
    """
    Returns a list of project PI usernames that are not eligible to be a PI.

    :param project_pi_usernames: A list of project PI usernames
    """
    users_info = get_users_info(project_pi_usernames)

    ineligible_pis = []
    for username, user_info in users_info.items():
        if not check_if_pi_eligible(username, user_info.get("memberOf", [])):
            ineligible_pis.append(username)

    return ineligible_pis
