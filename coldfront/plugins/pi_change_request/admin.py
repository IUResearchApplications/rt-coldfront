import textwrap

from django.contrib import admin

from coldfront.plugins.pi_change_request.models import (
    ProjectPiChangeRequest,
    ProjectPiChangeRequestResourceApproval,
    ProjectPiChangeRequestResourceApprovalSetting,
    ProjectPiChangeRequestResourceApprovalStatusChoice,
    ProjectPiChangeRequestReviewGroupTicketEmail,
    ProjectPiChangeRequestStatusChoice,
    ProjectPiChangeRequestUserApproval,
    ProjectPiChangeRequestUserApprovalStatusChoice,
)
from coldfront.plugins.pi_change_request.signals import (
    pi_change_request_created,
    pi_change_request_user_response,
)


@admin.register(ProjectPiChangeRequest)
class ProjectPiChangeRequestAdmin(admin.ModelAdmin):
    fields_add = ("project", "new_pi", "justification")
    # The change form is a deliberate escape hatch for repairing a request's state by hand.
    # Setting "status" here bypasses the center actions: in particular, flipping a request to
    # "Complete" does NOT apply the PI change (apply_pi_change only runs in the activation view).
    fields_change = ("project", "new_pi", "justification", "status", "resources")
    list_display = ("pk", "project_title", "new_pi", "status")
    list_filter = ("status", "resources")
    search_fields = ("new_pi__username", "new_pi__first_name", "new_pi__last_name", "project__title")
    raw_id_fields = ("new_pi", "project")
    filter_horizontal = ("resources",)

    def project_title(self, obj):
        return textwrap.shorten(obj.project.title, width=50)

    def get_fields(self, request, obj=None):
        if obj is None:
            return self.fields_add
        return self.fields_change

    def save_model(self, request, obj, form, change):
        """Mirror the center page creation flow: derive what an admin should not type by hand.

        Unlike the center page flow, no notifications are sent, but the creation signals
        still fire so integrations hear about admin-created requests too.
        """
        if not change:
            obj.current_pi = obj.project.pi
            obj.initiator = request.user
            obj.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("New")

        super().save_model(request, obj, form, change)

        if not change:
            obj.set_resources_from_active_allocations()
            obj.create_resource_approvals()
            user_approvals = obj.create_user_approvals([obj.current_pi, obj.new_pi])

            pi_change_request_created.send(sender=self.__class__, pi_change_request_pk=obj.pk)
            # An admin is normally not an approval party, but an admin who is the current or
            # new PI would have their approval recorded automatically, like the center flow.
            for approval in user_approvals:
                if approval.status.name == "Approved":
                    pi_change_request_user_response.send(
                        sender=self.__class__,
                        user_approval_pk=approval.pk,
                        pi_change_request_pk=obj.pk,
                    )


@admin.register(ProjectPiChangeRequestResourceApproval)
class ProjectPiChangeRequestResourceApprovalAdmin(admin.ModelAdmin):
    fields = ("request", "resource", "status", "handler", "reason")
    list_display = ("pk", "resource", "status", "handler")
    list_filter = ("status", "resource")
    search_fields = ("handler__username", "handler__first_name", "handler__last_name", "request__project__title")
    raw_id_fields = ("request", "resource", "handler")


@admin.register(ProjectPiChangeRequestResourceApprovalStatusChoice)
class ProjectPiChangeRequestResourceApprovalStatusChoiceAdmin(admin.ModelAdmin):
    list_display = ("name",)


@admin.register(ProjectPiChangeRequestResourceApprovalSetting)
class ProjectPiChangeRequestResourceApprovalSettingAdmin(admin.ModelAdmin):
    list_display = ("resource", "requires_approval")


@admin.register(ProjectPiChangeRequestStatusChoice)
class ProjectPiChangeRequestStatusChoiceAdmin(admin.ModelAdmin):
    list_display = ("name",)


@admin.register(ProjectPiChangeRequestUserApproval)
class ProjectPiChangeRequestUserApprovalAdmin(admin.ModelAdmin):
    list_display = ("pk", "request", "user", "status")
    list_filter = ("status",)
    search_fields = ("user__username", "user__first_name", "user__last_name")
    raw_id_fields = ("request", "user")


@admin.register(ProjectPiChangeRequestUserApprovalStatusChoice)
class ProjectPiChangeRequestUserApprovalStatusChoiceAdmin(admin.ModelAdmin):
    list_display = ("name",)


@admin.register(ProjectPiChangeRequestReviewGroupTicketEmail)
class ProjectPiChangeRequestReviewGroupTicketEmailAdmin(admin.ModelAdmin):
    list_display = ("group", "email")
    search_fields = ("group__name", "email")
