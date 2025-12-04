from coldfront.config.base import INSTALLED_APPS
from coldfront.config.env import ENV

INSTALLED_APPS += [
    "coldfront.plugins.request_forms",
]

REQUEST_FORMS_EMAILS = ENV.str("REQUEST_FORMS_EMAILS", default={})
