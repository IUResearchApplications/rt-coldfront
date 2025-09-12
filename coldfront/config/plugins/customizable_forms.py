from coldfront.config.base import INSTALLED_APPS
from coldfront.config.env import ENV


INSTALLED_APPS += [
    'coldfront.plugins.customizable_forms',
]

ADDITIONAL_CUSTOM_FORMS = ENV.list('ADDITIONAL_CUSTOM_FORMS', default=[])
ADDITIONAL_CUSTOM_GENERIC_FORM = ENV.str('ADDITIONAL_CUSTOM_GENERIC_FORM', default='')
ADDITIONAL_PERSISTANCE_FUNCTIONS = ENV.list('ADDITIONAL_PERSISTANCE_FUNCTIONS', default=[])
