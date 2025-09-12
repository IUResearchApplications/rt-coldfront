import re
import importlib

from coldfront.core.utils.common import import_from_settings

CUSTOMIZABLE_FORMS_ALLOCATION_VIEWS = import_from_settings(
    "CUSTOMIZABLE_FORMS_ALLOCATION_VIEWS", []
)

rules = {}


def standardize_resource_name(resource_name):
    return re.sub("[^A-Za-z0-9]+", "", resource_name)


def initialize_rules():
    for resource, values in CUSTOMIZABLE_FORMS_ALLOCATION_VIEWS.items():
        resource = standardize_resource_name(resource)
        rules[resource] = []
        for rule_func in values.get("rule_functions"):
            rule_func_module, rule_func_name = rule_func.rsplit(".", 1)
            rules[resource].append(
                getattr(importlib.import_module(rule_func_module), rule_func_name)
            )


def get_rules(resource):
    return rules.get(standardize_resource_name(resource))
