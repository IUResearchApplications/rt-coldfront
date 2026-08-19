import textwrap

from django.contrib import admin

from coldfront.plugins.pi_change_request.models import (
    ProjectPiChangeRequest,
    ProjectPiChangeRequestApproval,
    ProjectPiChangeRequestApprovalStatusChoice,
    ProjectPiChangeRequestRequiresApprovalSetting,
    ProjectPiChangeRequestStatusChoice,
)


@admin.register(ProjectPiChangeRequest)
class ProjectPiChangeRequestAdmin(admin.ModelAdmin):
    fields_change = ("project", "new_pi", "justification", "status", "resources")
    list_display = ("pk", "project_title", "new_pi", "status")
    list_filter = ("status", "resources")
    search_fields = ("new_pi__username", "new_pi__first_name", "new_pi__last_name", "project__title")
    raw_id_fields = ("new_pi", "project")
    filter_horizontal = ("resources",)

    def project_title(self, obj):
        return textwrap.shorten(obj.project.title, width=50)

    def get_fields(self, request, obj):
        if obj is None:
            return super().get_fields(request)
        else:
            return self.fields_change


@admin.register(ProjectPiChangeRequestApproval)
class ProjectPiChangeRequestApprovalAdmin(admin.ModelAdmin):
    fields_change = ("request", "resource", "status", "handler")
    list_display = ("pk", "resource", "status", "handler")
    list_filter = ("status", "resource")
    search_fields = ("handler__username", "handler__first_name", "handler__last_name", "request__project__title")
    raw_id_fields = ("handler",)


@admin.register(ProjectPiChangeRequestApprovalStatusChoice)
class ProjectPiChangeRequestApprovalStatusChoiceAdmin(admin.ModelAdmin):
    list_display = ("name",)


@admin.register(ProjectPiChangeRequestRequiresApprovalSetting)
class ProjectPiChangeRequestRequiresApprovalSettingAdmin(admin.ModelAdmin):
    list_display = ("resource", "requires_approval")


@admin.register(ProjectPiChangeRequestStatusChoice)
class ProjectPiChangeRequestStatusChoiceAdmin(admin.ModelAdmin):
    list_display = ("name",)
