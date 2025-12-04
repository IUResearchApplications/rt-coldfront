from coldfront.config.base import INSTALLED_APPS
from coldfront.config.env import ENV

INSTALLED_APPS += [
    "coldfront.plugins.update_user_profiles",
]

UPDATE_USER_PROFILES_UPDATE_STATUSES = ENV.bool("UPDATE_USER_PROFILES_UPDATE_STATUSES", default=False)
