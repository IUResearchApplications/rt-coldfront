from coldfront.core.user.models import UserProfile


def update_all_user_profiles():
    """
    Updates all user profiles.
    """
    try:
        from coldfront.plugins.ldap_misc.utils.ldap_user_search import get_user_info
        from coldfront.plugins.ldap_user_search.utils import LDAPUserSearch
    except ImportError:
        return

    ldap_search = LDAPUserSearch(None, None)
    user_profiles = UserProfile.objects.select_related("user").all()
    for user_profile in user_profiles:
        attributes = get_user_info(user_profile.user.username, ldap_search)

        save_changes = False
        for name, value in attributes.items():
            if name == "title" and value == "group":
                user_profile.is_pi = False
            user_profile_attr = getattr(user_profile, name, None)
            if user_profile_attr and not user_profile_attr == value:
                setattr(user_profile, name, value)
                continue
            user_attr = getattr(user_profile.user, name, None)
            if user_attr and not user_attr == value:
                setattr(user_profile.user, name, value)

        if save_changes:
            user_profile.save()
