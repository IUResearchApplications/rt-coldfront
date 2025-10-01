from django.apps import AppConfig


class ProjectConfig(AppConfig):
    name = 'coldfront.core.project'

    # def ready(self):
    #     initialize_auto_approval_funcs()