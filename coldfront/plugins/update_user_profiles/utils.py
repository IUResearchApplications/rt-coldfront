from django.conf import settings

from coldfront.core.user.models import UserProfile
from coldfront.core.utils.common import get_users_info

if "coldfront.plugins.ldap_misc" in settings.INSTALLED_APPS:
    from coldfront.plugins.ldap_misc.utils.ldap_user_search import get_users_info


def update_all_user_profiles():
    """
    Updates all user profiles.
    """
    user_profiles = UserProfile.objects.select_related("user").all()
    users_info = get_users_info([user_profile.user.username for user_profile in user_profiles])
    for user_profile in user_profiles:
        attributes = users_info.get(user_profile.user.username)
        if attributes is None:
            # If one is None then all are None
            return

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
