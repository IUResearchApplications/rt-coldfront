from django.conf import settings
from django.urls import reverse

from coldfront.core.resource.models import Resource
from coldfront.core.utils.mail import send_email_template
from coldfront.core.utils.slack import send_message
from coldfront.plugins.pi_change_request.models import ProjectPiChangeRequestReviewGroupTicketEmail


def full_name_with_username(user):
    """Display a user as "First Last (username)", the bare username when no name is set, or an em dash for None."""
    if user is None:
        return "—"
    full_name = user.get_full_name().strip()
    return f"{full_name} ({user.username})" if full_name else user.username


def send_slack_message(project_obj, url):
    if not settings.SLACK_MESSAGING_ENABLED:
        return

    send_message(
        f'A new PI change request for project "{project_obj.title}" with id {project_obj.pk} has been submitted. You can view it here: {url}'
    )


def send_email(subject, template, template_context, receiver=None):
    """Send a template email to one or more receivers, defaulting to the center alerts address.

    A string receiver is treated as a single address; any other iterable is normalized to a list,
    which is what core's email helpers expect.
    """
    if not settings.EMAIL_ENABLED:
        return

    if receiver is None:
        receiver = [settings.EMAIL_ALERTS_EMAIL_ADDRESS]
    elif isinstance(receiver, str):
        receiver = [receiver]
    else:
        receiver = list(receiver)

    send_email_template(subject, template, template_context, receiver)


def get_participant_email_addresses(pi_change_request):
    """Return the deduplicated email addresses of the request's participants (current PI, new PI, and initiator)."""
    users = (pi_change_request.current_pi, pi_change_request.new_pi, pi_change_request.initiator)
    return {user.email for user in users if user.email}


def send_ready_email(pi_change_request, url):
    """Notify the center that a PI change request has all its approvals and is ready to activate."""
    template_context = {
        "project_title": pi_change_request.project.title,
        "project_id": pi_change_request.project.pk,
        "url": url,
        "help_email": settings.EMAIL_TICKET_SYSTEM_ADDRESS,
    }
    send_email(
        "PI Change Request Ready for Activation",
        "pi_change_request/email/pi_change_request_ready.txt",
        template_context,
    )


def send_blocked_email(pi_change_request, domain_url, blocker):
    """Notify the involved parties that a denied approval has blocked a PI change request.

    The blocker is a short lowercase phrase describing what was denied, formatted into a sentence by
    the template. The email links to the project page, since the recipients cannot access the center.
    """
    project_url = "{}{}".format(domain_url, reverse("project-detail", kwargs={"pk": pi_change_request.project.pk}))
    template_context = {
        "project_title": pi_change_request.project.title,
        "project_id": pi_change_request.project.pk,
        "current_pi": pi_change_request.current_pi,
        "new_pi": pi_change_request.new_pi,
        "blocker": blocker,
        "project_url": project_url,
        "help_email": settings.EMAIL_TICKET_SYSTEM_ADDRESS,
    }
    receivers = get_participant_email_addresses(pi_change_request)

    send_email(
        "Your Project PI Change Request Was Blocked",
        "pi_change_request/email/pi_change_request_blocked.txt",
        template_context,
        receivers,
    )


def send_user_approval_notifications(pi_change_request, user_approvals, domain_url):
    """Email each approver whose response is still needed on a new PI change request.

    Approvals that already have a response, such as the initiator's own auto-approved
    approval, are skipped.
    """
    for approval in user_approvals:
        if approval.status.name != "Pending":
            continue

        if not approval.user.email:
            continue

        url = "{}{}".format(domain_url, reverse("pi-change-request-user", kwargs={"pk": approval.pk}))
        template_context = {
            "user": approval.user,
            "initiator": pi_change_request.initiator,
            "current_pi": pi_change_request.current_pi,
            "new_pi": pi_change_request.new_pi,
            "project_title": pi_change_request.project.title,
            "project_id": pi_change_request.project.pk,
            "url": url,
            "help_email": settings.EMAIL_TICKET_SYSTEM_ADDRESS,
        }
        send_email(
            f'Action Required: PI Change Request for "{pi_change_request.project.title}"',
            "pi_change_request/email/pi_change_request_user_approval.txt",
            template_context,
            approval.user.email,
        )


def send_resource_approval_notifications(pi_change_request, resource_approvals, domain_url, review_permission):
    """Email each mapped ticket queue once with all resources on the request its group can approve.

    The review_permission is a dotted "app_label.codename" string identifying the review groups
    that may respond. Groups without a mapped ticket email are skipped; the center already
    receives the general new request email.
    """
    url = "{}{}".format(domain_url, reverse("pi-change-request-center"))
    app_label, codename = review_permission.split(".", 1)

    resource_by_id = {approval.resource.pk: approval.resource for approval in resource_approvals}
    resource_group_pairs = list(
        Resource.objects.filter(
            pk__in=resource_by_id,
            review_groups__permissions__codename=codename,
            review_groups__permissions__content_type__app_label=app_label,
        )
        .values_list("id", "review_groups__id")
        .distinct()
    )
    group_ids = {group_id for _, group_id in resource_group_pairs}
    email_by_group_id = dict(
        ProjectPiChangeRequestReviewGroupTicketEmail.objects.filter(group_id__in=group_ids).values_list(
            "group_id", "email"
        )
    )

    resources_by_receiver = {}
    for resource_id, group_id in resource_group_pairs:
        email = email_by_group_id.get(group_id)
        if email:
            resources_by_receiver.setdefault(email, []).append(resource_by_id[resource_id])

    for receiver, resources in resources_by_receiver.items():
        template_context = {
            "current_pi": pi_change_request.current_pi,
            "new_pi": pi_change_request.new_pi,
            "project_title": pi_change_request.project.title,
            "project_id": pi_change_request.project.pk,
            "resources": resources,
            "url": url,
            "help_email": settings.EMAIL_TICKET_SYSTEM_ADDRESS,
        }
        send_email(
            f'Action Required: PI Change Request Resource Approval for "{pi_change_request.project.title}"',
            "pi_change_request/email/pi_change_request_resource_approval.txt",
            template_context,
            [receiver],
        )
