from django import template

from coldfront.plugins.pi_change_request.models import (
    ACTIVE_REQUEST_STATUSES,
    ProjectPiChangeRequest,
    ProjectPiChangeRequestUserApproval,
)
from coldfront.plugins.pi_change_request.permissions import actionable_resource_approvals

register = template.Library()


@register.filter
def full_name_with_username(user):
    """Display a user as "First Last (username)", falling back to the username when no name is set.

    A missing user (None) renders as an em dash, for historical records without a handler.
    """
    if user is None:
        return "—"
    full_name = user.get_full_name().strip()
    return f"{full_name} ({user.username})" if full_name else user.username


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
