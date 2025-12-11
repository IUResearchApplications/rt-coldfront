import json
import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_save
from django.dispatch import receiver
from django_cas_ng.signals import cas_user_authenticated, cas_user_logout

from coldfront.core.user.models import UserProfile
from coldfront.core.utils.common import get_user_info, import_from_settings

if "coldfront.plugins.ldap_misc" in settings.INSTALLED_APPS:
    from coldfront.plugins.ldap_misc.utils.ldap_user_search import get_user_info

logger = logging.getLogger(__name__)

ADDITIONAL_USER_SEARCH_CLASSES = import_from_settings("ADDITIONAL_USER_SEARCH_CLASSES", [])


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        user_profile = UserProfile.objects.create(user=instance, title="", department="", division="")
        attributes = get_user_info(instance.username)
        if not attributes:
            return
        user_profile = instance.userprofile
        for name, value in attributes.items():
            if name == "title" and not value == "group":
                user_profile.is_pi = True
            user_profile_attr = getattr(user_profile, name, None)
            if user_profile_attr is not None and not user_profile_attr == value:
                setattr(user_profile, name, value)
                continue
            user_attr = getattr(instance, name, None)
            if user_attr is not None and not user_attr == value:
                setattr(instance, name, value)

        instance.save()


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    instance.userprofile.save()


@receiver(user_logged_in, sender=User)
def update_user_profile(sender, user, **kwargs):
    logger.info(f"{user.username} logged in")
    attributes = get_user_info(user.username)
    if not attributes:
        return
    save_changes = False
    if not user.email == attributes.get("email"):
        user.email = attributes.get("email")
        save_changes = True

    user_profile = user.userprofile
    for name, value in attributes.items():
        user_profile_attr = getattr(user_profile, name, None)
        if user_profile_attr is not None and not user_profile_attr == value:
            setattr(user_profile, name, value)
            save_changes = True

    if not user_profile.user.email == attributes.get("email"):
        user_profile.user.email = attributes.get("email")
        user_profile.user.save()

    if save_changes:
        user.save()


@receiver(cas_user_authenticated)
def cas_user_authenticated_callback(sender, **kwargs):
    args = {}
    args.update(kwargs)
    print(
        """cas_user_authenticated_callback:
    user: %s
    created: %s
    attributes: %s
    """
        % (args.get("user"), args.get("created"), json.dumps(args.get("attributes"), sort_keys=True, indent=2))
    )


@receiver(cas_user_logout)
def cas_user_logout_callback(sender, **kwargs):
    args = {}
    args.update(kwargs)
    print(
        """cas_user_logout_callback:
    user: %s
    session: %s
    ticket: %s
    """
        % (args.get("user"), args.get("session"), args.get("ticket"))
    )
