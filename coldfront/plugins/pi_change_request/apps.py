import importlib

from django.apps import AppConfig


class PiChangeRequestConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "coldfront.plugins.pi_change_request"

    def ready(self) -> None:
        importlib.import_module("coldfront.plugins.pi_change_request.signals")
