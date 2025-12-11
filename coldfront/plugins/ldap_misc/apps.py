from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class LdapMiscConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "coldfront.plugins.ldap_misc"

    def ready(self):
        if "coldfront.plugins.ldap_user_search.utils.LDAPUserSearch" not in settings.ADDITIONAL_USER_SEARCH_CLASSES:
            raise ImproperlyConfigured("ldap_misc requires the ldap_user_search plugin, please enable it")
