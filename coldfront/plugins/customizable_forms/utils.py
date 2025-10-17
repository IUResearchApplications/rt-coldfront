import importlib
import re

from coldfront.core.utils.common import import_from_settings

CUSTOMIZABLE_FORMS_ALLOCATION_VIEWS = import_from_settings("CUSTOMIZABLE_FORMS_ALLOCATION_VIEWS", [])
CUSTOMIZABLE_FORMS_ADDITIONAL_PERSISTENCE_FUNCTIONS = import_from_settings(
    "CUSTOMIZABLE_FORMS_ADDITIONAL_PERSISTENCE_FUNCTIONS", {}
)

rule_functions = {}
persistence_functions = {}


def standardize_resource_name(resource_name):
    return re.sub("[^A-Za-z0-9]+", "", resource_name)


def initialize_rule_functions():
    for resource, values in CUSTOMIZABLE_FORMS_ALLOCATION_VIEWS.items():
        resource = standardize_resource_name(resource)
        rule_functions[resource] = []
        for func in values.get("rule_functions"):
            func_module, func_name = func.rsplit(".", 1)
            rule_functions[resource].append(getattr(importlib.import_module(func_module), func_name))


def initialize_persistence_functions():
    for variable, func in CUSTOMIZABLE_FORMS_ADDITIONAL_PERSISTENCE_FUNCTIONS.items():
        func_module, func = func.rsplit(".", 1)
        persistence_functions[variable] = getattr(importlib.import_module(func_module), func)


def get_rule_functions(resource):
    return rule_functions.get(standardize_resource_name(resource))


def get_persistence_functions():
    return persistence_functions
