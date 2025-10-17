from django.contrib import admin

from coldfront.core.allocation.admin import ResourceFilter, ReviewGroupFilteredResourceQueryset
from coldfront.plugins.allocation_removal_requests.models import (
    AllocationRemovalRequest,
    AllocationRemovalStatusChoice,
)


# Register your models here.
@admin.register(AllocationRemovalRequest)
class AllocationRemovalRequestAdmin(ReviewGroupFilteredResourceQueryset):
    list_display = ("pk", "allocation_pk", "project_pi", "requestor", "allocation_prior_status", "resource", "status")
    readonly_fields = ("project_pi", "requestor", "allocation_prior_status", "allocation")
    list_filter = ("status", ResourceFilter, "allocation_prior_status")
    search_fields = (
        "requestor__username",
        "requestor__first_name",
        "requestor__last_name",
    )

    def resource(self, obj):
        allocation_obj = obj.allocation
        return allocation_obj.get_parent_resource.name

    def allocation_pk(self, obj):
        allocation_obj = obj.allocation
        return allocation_obj.pk


@admin.register(AllocationRemovalStatusChoice)
class AllocationRemovalStatusChoiceAdmin(admin.ModelAdmin):
    list_display = ("pk", "name")
