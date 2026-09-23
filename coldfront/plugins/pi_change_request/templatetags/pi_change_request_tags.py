from django import template

from coldfront.plugins.pi_change_request.models import (
    ACTIVE_REQUEST_STATUSES,
    ProjectPiChangeRequest,
    ProjectPiChangeRequestUserApproval,
)
from coldfront.plugins.pi_change_request.permissions import actionable_resource_approvals
from coldfront.plugins.pi_change_request.utils import full_name_with_username

register = template.Library()

register.filter(full_name_with_username)


@register.simple_tag
def pi_change_pending_approvals_count(user):
    """Return the number of resource approvals awaiting this user's response."""
    if not user.is_authenticated:
        return 0
    return actionable_resource_approvals(user).count()


@register.simple_tag
def active_pi_change_request(project):
    """Return the in-progress PI change request for this project, if any."""
    return (
        ProjectPiChangeRequest.objects.filter(project=project, status__name__in=ACTIVE_REQUEST_STATUSES)
        .select_related("status")
        .first()
    )


@register.simple_tag
def pi_change_user_approval(project, user):
    """Return the user's approval row on this project's in-progress PI change request, if any."""
    return (
        ProjectPiChangeRequestUserApproval.objects.filter(
            request__project=project, request__status__name__in=ACTIVE_REQUEST_STATUSES, user=user
        )
        .select_related("request", "status")
        .first()
    )
