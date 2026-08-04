import datetime
import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import Prefetch

from coldfront.core.allocation.models import Allocation, AllocationAttribute, AllocationUser
from coldfront.core.project.models import Project, ProjectStatusChoice, ProjectUser
from coldfront.core.project.utils import get_ineligible_pis
from coldfront.core.resource.models import Resource
from coldfront.core.utils.common import import_from_settings
from coldfront.core.utils.mail import send_email_template

if "coldfront.plugins.ldap_misc" in settings.INSTALLED_APPS:
    from coldfront.plugins.ldap_misc.utils.project import get_ineligible_pis

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
    users = User.objects.all().prefetch_related(
        Prefetch(
            lookup="projectuser_set",
            queryset=ProjectUser.objects.filter(
                status__name="Active",
                project__status__name="Active",
                project__requires_review=True,
                enable_notifications=True,
            )
            .select_related(
                "role",
                "status",
                "project",
                "project__status",
                "project__type",
                "project__pi",
            )
            .prefetch_related(
                Prefetch(
                    lookup="project__allocation_set",
                    queryset=Allocation.objects.filter(status__name="Active")
                    .select_related("status")
                    .prefetch_related(
                        Prefetch(lookup="resources", queryset=Resource.objects.all().select_related("resource_type")),
                        Prefetch(
                            lookup="allocationattribute_set",
                            queryset=AllocationAttribute.objects.all().select_related("allocation_attribute_type"),
                        ),
                        Prefetch(
                            lookup="allocationuser_set",
                            queryset=AllocationUser.objects.all().select_related("user", "status"),
                        ),
                        "allocationuser_set",
                    ),
                    to_attr="project_allocations",
                )
            ),
            to_attr="active_user_projects",
        ),
    )
    for user in users:
        for days_remaining in sorted(set(EMAIL_PROJECT_EXPIRING_NOTIFICATION_DAYS)):
            projects = []
            expiring_in_days = (datetime.datetime.today() + datetime.timedelta(days=days_remaining)).date()

            for project_user in user.active_user_projects:
                if not project_user.role.name == "Manager":
                    continue

                project = project_user.project
                if not project.end_date == expiring_in_days:
                    continue

                project_url = f"{CENTER_BASE_URL.strip('/')}/{'project'}/{project.pk}/"

                allocations = project.project_allocations

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
                    [user.email],
                    EMAIL_TICKET_SYSTEM_ADDRESS,
                )

                logger.debug(f"Project(s) expiring email sent to user {user}.")

    # Expired projects
    for user in users:
        expiring_in_days = (datetime.datetime.today() + datetime.timedelta(days=-1)).date()
        projects = []
        for project_user in user.active_user_projects:
            project = project_user.project

            if not project.end_date == expiring_in_days:
                continue

            project_url = f"{CENTER_BASE_URL.strip('/')}/{'project'}/{project.pk}/"

            allocations = []
            for allocation in project.project_allocations:
                if project_user.role.name == "Manager":
                    allocations.append(allocation)
                    continue

                for allocation_user in allocation.allocationuser_set.all():
                    if not project_user.user == allocation_user.user:
                        continue
                    if allocation_user.status.name not in ["Active", "Invited", "Disabled"]:
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
                [user.email],
                EMAIL_TICKET_SYSTEM_ADDRESS,
            )

            logger.debug(f"Project(s) expired email sent to user {user}.")


def check_ineligible_pis():
    logger.info("Checking PI eligibilities...")
    ineligible_pis = get_ineligible_pis(
        Project.objects.filter(status__name="Active").values_list("pi__username", flat=True)
    )
    if ineligible_pis:
        logger.warning(f"PIs {', '.join(ineligible_pis)} are no longer eligible to be PIs")
    logger.info("Done checking PI eligibilities")
