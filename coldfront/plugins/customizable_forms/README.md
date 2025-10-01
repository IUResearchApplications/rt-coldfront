# Customizable Forms
## Overview
This plugin handles importing and displaying external allocation forms. It creates a new page that lists all available resources to a user. Rules can be created for each resource if criteria needs to be met for a user to request it. Persistent variables can be added to reduce duplicate calculations for variables that are common between rules. If a resource does not have a custom form for it then the generic view is used.

## Setup
To use this plugin you need to add this line to your env file:
```
PLUGIN_CUSTOMIZABLE_FORMS=True
```

## Environment Variables
There are three environment variables that can be set:
* CUSTOMIZABLE_FORMS_GENERIC_ALLOCATION_VIEW (str) - Module path to custom generic view class. This overrides ColdFront's view class for allocation requests.
* CUSTOMIZABLE_FORMS_ALLOCATION_VIEWS (dict) - A dictionary with an entry for each custom allocation form. You need to provide the module path to the view class. Optionally you can provide an `info_url` that points to a website with more information about the resource and a `rule_functions` with module paths to the functions that restrict users access to requesting the resource.
* CUSTOMIZABLE_FORMS_ADDITIONAL_PERSISTENCE_FUNCTIONS (dict) - A dictionary of the variable name as the key and the value being the module path to the function that calculates its value.

## Environment Variable Examples
```
CUSTOMIZABLE_FORMS_GENERIC_ALLOCATION_VIEW = "module.path.to.views.GenericView"

CUSTOMIZABLE_FORMS_ALLOCATION_VIEWS {
    "resource name": {
            "view": "module.path.to.ClassView",
            "info_url": "https://example.com",
            "rule_functions": [
                "module.path.to.rule.function1",
                "module.path.to.rule.function2"
            ],
    }
}

CUSTOMIZABLE_FORMS_ADDITIONAL_PERSISTENCE_FUNCTIONS = {
    "var_name1": "module.path.to.function1",
    "var_name2": "module.path.to.function2"
}
```

## Example Use TODO
Here is a simple example of a custom view, form, rule, and persistence function and how they can be set up to be used in with the plugin. Assume these exist within an external plugin.