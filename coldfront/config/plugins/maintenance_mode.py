from coldfront.config.base import INSTALLED_APPS, MIDDLEWARE

INSTALLED_APPS += [
    "coldfront.plugins.maintenance_mode",
]

MIDDLEWARE += ["coldfront.plugins.maintenance_mode.middleware.MaintenanceModeMiddleware"]
