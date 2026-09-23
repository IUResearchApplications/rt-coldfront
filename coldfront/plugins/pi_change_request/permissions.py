"""Permission constants and access checks for the PI change request plugin."""

from coldfront.core.resource.models import Resource
from coldfront.core.utils.groups import check_if_groups_in_review_groups
from coldfront.plugins.pi_change_request.models import ProjectPiChangeRequestResourceApproval

RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION = "pi_change_request.change_projectpichangerequestresourceapprovalsetting"
RESOURCE_APPROVAL_CHANGE_PERMISSION = "pi_change_request.change_projectpichangerequestresourceapproval"
PI_CHANGE_REQUEST_VIEW_PERMISSION = "pi_change_request.view_projectpichangerequest"
PI_CHANGE_REQUEST_CHANGE_PERMISSION = "pi_change_request.change_projectpichangerequest"

# check_if_groups_in_review_groups matches bare codenames, unlike has_perm which takes the dotted form.
RESOURCE_APPROVAL_SETTING_CHANGE_CODENAME = RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION.rpartition(".")[2]
RESOURCE_APPROVAL_CHANGE_CODENAME = RESOURCE_APPROVAL_CHANGE_PERMISSION.rpartition(".")[2]


def resource_actionable_by(user, user_groups, resource, permission_codename):
    """Whether the user may act on the resource's approvals or settings.

    A resource with review groups requires the user to belong to one of them holding the permission.
    A resource without review groups is open to staff users only.
    """
    review_groups = resource.review_groups.all()
    if not review_groups.exists():
        return user.is_staff
    return check_if_groups_in_review_groups(review_groups, user_groups, permission_codename)


def managed_resource_ids(user):
    """Return ids of resources this user may manage approvals for, or None for all resources.

    A resource is manageable if the user belongs to one of its review groups holding the
    resource approval change permission. Resources without review groups are open to
    staff users only.
    """
    if user.is_superuser:
        return None

    user_group_ids = list(user.groups.values_list("id", flat=True))
    resource_ids = set()
    if user_group_ids:
        resource_ids.update(
            Resource.objects.filter(
                review_groups__id__in=user_group_ids,
                review_groups__permissions__codename=RESOURCE_APPROVAL_CHANGE_CODENAME,
            ).values_list("id", flat=True)
        )

    if user.is_staff:
        resource_ids.update(Resource.objects.filter(review_groups__isnull=True).values_list("id", flat=True))

    return resource_ids


def actionable_resource_approvals(user):
    """Return pending resource approvals this user may respond to."""
    approvals = ProjectPiChangeRequestResourceApproval.objects.filter(
        status__name="Pending", request__status__name__in=["New", "Awaiting Approvals"]
    )

    resource_ids = managed_resource_ids(user)
    if resource_ids is not None:
        approvals = approvals.filter(resource_id__in=resource_ids)

    return approvals
