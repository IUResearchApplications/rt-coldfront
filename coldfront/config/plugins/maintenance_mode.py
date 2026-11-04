from coldfront.config.base import INSTALLED_APPS, MIDDLEWARE
from coldfront.config.env import ENV, PROJECT_ROOT

INSTALLED_APPS += [
    "coldfront.plugins.maintenance_mode",
]

MIDDLEWARE += ["coldfront.plugins.maintenance_mode.middleware.MaintenanceModeMiddleware"]


MAINTENANCE_MODE_BASE_DIR = ENV.str("MAINTENANCE_MODE_BASE_DIR", default=PROJECT_ROOT("var/maintenance_mode"))
