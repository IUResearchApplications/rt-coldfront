from django import template

from coldfront.plugins.pi_change_request.models import (
    ACTIVE_REQUEST_STATUSES,
    ProjectPiChangeRequest,
    ProjectPiChangeRequestUserApproval,
)

register = template.Library()


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
