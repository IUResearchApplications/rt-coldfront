from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from coldfront.core.utils.common import import_from_settings

ADDITIONAL_USER_SEARCH_CLASSES = import_from_settings("ADDITIONAL_USER_SEARCH_CLASSES", [])


class LdapMiscConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "coldfront.plugins.ldap_misc"

    def ready(self):
        if "coldfront.plugins.ldap_user_search.utils.LDAPUserSearch" not in ADDITIONAL_USER_SEARCH_CLASSES:
            raise ImproperlyConfigured("ldap_misc requires the ldap_user_search plugin, please enable it")
