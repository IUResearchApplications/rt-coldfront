import datetime
import logging

from django.contrib.auth.models import User

from coldfront.core.project.models import Project, ProjectStatusChoice
from coldfront.core.project.utils import check_if_pi_eligible
from coldfront.core.utils.common import import_from_settings
from coldfront.core.utils.mail import send_email_template

logger = logging.getLogger(__name__)

CENTER_NAME = import_from_settings("CENTER_NAME")
CENTER_BASE_URL = import_from_settings("CENTER_BASE_URL")
CENTER_PROJECT_RENEWAL_HELP_URL = import_from_settings("CENTER_PROJECT_RENEWAL_HELP_URL")
EMAIL_ENABLED = import_from_settings("EMAIL_ENABLED")

if EMAIL_ENABLED:
    EMAIL_SENDER = import_from_settings("EMAIL_SENDER")
    EMAIL_OPT_OUT_INSTRUCTION_URL = import_from_settings("EMAIL_OPT_OUT_INSTRUCTION_URL")
    EMAIL_SIGNATURE = import_from_settings("EMAIL_SIGNATURE")
    EMAIL_PROJECT_EXPIRING_NOTIFICATION_DAYS = import_from_settings(
        "EMAIL_PROJECT_EXPIRING_NOTIFICATION_DAYS",
        [
            7,
        ],
    )
    EMAIL_TICKET_SYSTEM_ADDRESS = import_from_settings("EMAIL_TICKET_SYSTEM_ADDRESS")

ADDITIONAL_USER_SEARCH_CLASSES = import_from_settings("ADDITIONAL_USER_SEARCH_CLASSES", [])


def update_statuses():
    expired_status_choice = ProjectStatusChoice.objects.get(name="Expired")
    projects_to_expire = Project.objects.filter(
        status__name="Active", end_date__lt=datetime.datetime.now().date(), requires_review=True
    )
    for project in projects_to_expire:
        project.status = expired_status_choice
        project.save()

    logger.info(f"Projects set to expired: {projects_to_expire.count()}")


def send_expiry_emails():
    if not EMAIL_ENABLED:
        return

    # Expiring projects
    for user in User.objects.all():
        for days_remaining in sorted(set(EMAIL_PROJECT_EXPIRING_NOTIFICATION_DAYS)):
            projects = []
            expiring_in_days = (datetime.datetime.today() + datetime.timedelta(days=days_remaining)).date()

            for project_user in user.projectuser_set.filter(status__name="Active"):
                if not project_user.enable_notifications:
                    continue

                project = project_user.project
                if project.status.name == "Active" and (project.end_date == expiring_in_days):
                    if not project.requires_review:
                        continue

                    project_url = f"{CENTER_BASE_URL.strip('/')}/{'project'}/{project.pk}/"

                    allocations = []
                    for allocation in project.allocation_set.filter(status__name="Active"):
                        if not project_user.role.name == "Manager":
                            allocation_user = allocation.allocationuser_set.filter(
                                status__name__in=["Active", "Invited", "Disabled"], user=user
                            )
                            if not allocation_user.exists():
                                continue

                        allocations.append(allocation)

                    projects.append(
                        {
                            "project": project,
                            "project_url": project_url,
                            "expiring_in_days": expiring_in_days,
                            "allocations": allocations,
                        }
                    )

            if projects:
                template_context = {
                    "center_name": CENTER_NAME,
                    "expiring_in_days": days_remaining,
                    "project_dict": projects,
                    "project_renewal_help_url": CENTER_PROJECT_RENEWAL_HELP_URL,
                    "help_email": EMAIL_TICKET_SYSTEM_ADDRESS,
                    "signature": EMAIL_SIGNATURE,
                }
                send_email_template(
                    f"Access to your {CENTER_NAME} projects is expiring soon",
                    "email/project_expiring.txt",
                    template_context,
                    EMAIL_TICKET_SYSTEM_ADDRESS,
                    [user.email],
                )

                logger.debug(f"Project(s) expiring email sent to user {user}.")

    # Expired projects
    for user in User.objects.all():
        expiring_in_days = (datetime.datetime.today() + datetime.timedelta(days=-1)).date()

        for project_user in user.projectuser_set.filter(status__name="Active"):
            projects = []
            project = project_user.project

            if project.status.name == "Active" and (project.end_date == expiring_in_days):
                if not project.requires_review:
                    continue

                project_url = f"{CENTER_BASE_URL.strip('/')}/{'project'}/{project.pk}/"

                allocations = []
                for allocation in project.allocation_set.filter(status__name="Active"):
                    if not project_user.role.name == "Manager":
                        allocation_user = allocation.allocationuser_set.filter(
                            status__name__in=["Active", "Invited", "Disabled"], user=user
                        )
                        if not allocation_user.exists():
                            continue

                    allocations.append(allocation)

                projects.append({"project": project, "project_url": project_url, "allocations": allocations})

        if projects:
            template_context = {
                "center_name": CENTER_NAME,
                "project_dict": projects,
                "project_renewal_help_url": CENTER_PROJECT_RENEWAL_HELP_URL,
                "help_email": EMAIL_TICKET_SYSTEM_ADDRESS,
                "signature": EMAIL_SIGNATURE,
            }
            send_email_template(
                f"Access to your {CENTER_NAME} projects has expired",
                "email/project_expired.txt",
                template_context,
                EMAIL_TICKET_SYSTEM_ADDRESS,
                [user.email],
            )

            logger.debug(f"Project(s) expired email sent to user {user}.")


def check_current_pi_eligibilities():
    try:
        from coldfront.plugins.ldap_misc.utils.project import check_current_pi_eligibilities

        logger.info("Checking PI eligibilities...")
        ineligible_pis = check_current_pi_eligibilities(Project.objects.filter(status__name="Active"))
        logger.warning(f"PIs {', '.join(ineligible_pis)} are no longer eligible to be PIs")
        logger.info("Done checking PI eligibilities")
    except ImportError:
        logger.warning("ldap_misc plugin not enabled, skipping PI eligibility check")
