from django.conf import settings

from coldfront.core.utils.mail import send_email_template
from coldfront.core.utils.slack import send_message


def send_slack_message(project_obj, url):
    if not settings.SLACK_MESSAGING_ENABLED:
        return

    send_message(
        f'A new PI change request for project "{project_obj.title}" with id {project_obj.pk} has been submitted. You can view it here: {url}'
    )


def send_email(subject, template, template_context, receiver=None):
    """Send a template email to one or more receivers, defaulting to the center alerts address."""
    if not settings.EMAIL_ENABLED:
        return

    if receiver is None:
        receiver = [settings.EMAIL_ALERTS_EMAIL_ADDRESS]
    elif isinstance(receiver, str):
        receiver = [receiver]

    send_email_template(subject, template, template_context, receiver)


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
