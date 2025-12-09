import importlib.util

from django.core.exceptions import ImproperlyConfigured

from coldfront.config.env import ENV

if importlib.util.find_spec("coldfront.plugins.ldap_user_search.utils") is None:
    raise ImproperlyConfigured("Please enable the ldap_user_search plugin")


LDAP_ENABLE_PROJECT_PI_ELIGIBLE_ADS_GROUPS = ENV.bool("LDAP_ENABLE_PROJECT_PI_ELIGIBLE_ADS_GROUPS", default=False)
LDAP_PROJECT_PI_ELIGIBLE_ADS_GROUPS = ENV.list("LDAP_PROJECT_PI_ELIGIBLE_ADS_GROUPS")
