from django.conf import settings

from coldfront.core.project.models import ProjectUserRoleChoice
from coldfront.plugins.ldap_misc.utils.ldap_user_search import get_users_info


def check_if_pis_eligible(project_pi_usernames: list[str]) -> dict:
    """Checks the elgibility of project PIs.

    Params:
        project_pi_usernames (list): a list of project PI usernames

    Returns:
        dict: a dictionary of project PIs' eligibilities, if LDAP_ENABLE_PROJECT_PI_ELIGIBLE_ADS_GROUPS is False then
        it's empty
    """
    if not settings.LDAP_ENABLE_PROJECT_PI_ELIGIBLE_ADS_GROUPS:
        return {}

    eligible_statuses = {}
    users_info = get_users_info(project_pi_usernames)
    for username, user_info in users_info.items():
        if user_info is None:
            eligible_statuses[username] = True
            continue
        for user_membersip in user_info.get("memberOf", []):
            eligible = user_membersip in settings.LDAP_PROJECT_PI_ELIGIBLE_ADS_GROUPS
            eligible_statuses[username] = eligible
            if eligible:
                break

    return eligible_statuses


def get_ineligible_pis(project_pi_usernames: list[str]) -> list[str]:
    """Finds project PIs that are not eligible to be a PI.

    Params:
        project_pi_usernames (list): a list of project PI usernames

    Returns:
        list: a list of project PI usernames that are not eligible to be a PI
    """
    ineligible_pis = []
    for username, eligible in check_if_pis_eligible(project_pi_usernames).items():
        if not eligible:
            ineligible_pis.append(username)

    return ineligible_pis


def update_project_user_matches(matches: list[dict]) -> list:
    """Update the roles in each match based on if they are a group account.

    Params:
        matches (list): a list of matches found

    Returns:
        list: the list of matches with their updated roles
    """
    users_info = get_users_info([match.get("username") for match in matches])
    for match in matches:
        user_info = users_info.get(match.get("username"))
        role = "Group" if user_info is not None and user_info.get("title") == "group" else "User"
        match.update({"role": ProjectUserRoleChoice.objects.get(name=role)})

    return matches
