from coldfront.core.utils.common import import_from_settings
from coldfront.core.utils.mail import send_email_template
from coldfront.core.utils.slack import send_message

EMAIL_ENABLED = import_from_settings("EMAIL_ENABLED", False)
EMAIL_ALERTS_EMAIL_ADDRESS = import_from_settings("EMAIL_ALERTS_EMAIL_ADDRESS", "")
SLACK_MESSAGING_ENABLED = import_from_settings("SLACK_MESSAGING_ENABLED", False)


def send_slack_message(project_obj, url):
    if not SLACK_MESSAGING_ENABLED:
        return

    send_message(
        f'A new PI change request for project "{project_obj.title}" with id {project_obj.pk} has been submitted. You can view it here: {url}'
    )


def send_email(subject, template, template_context, receiver=EMAIL_ALERTS_EMAIL_ADDRESS):
    if not EMAIL_ENABLED:
        return

    send_email_template(subject, template, template_context, [receiver])
