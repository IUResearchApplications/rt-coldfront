from django.apps import AppConfig


class CustomizableFormsConfig(AppConfig):
    name = "coldfront.plugins.customizable_forms"

    def ready(self):
        from coldfront.plugins.customizable_forms.urls import (
            add_additional_forms,
            replace_generic_form,
        )

        add_additional_forms()
        replace_generic_form()
