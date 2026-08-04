# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import datetime

# import the logging library
import logging

from django.db.models import F
from django.db.models.query import Prefetch

from coldfront.core.allocation.models import Allocation, AllocationStatusChoice, AllocationUser
from coldfront.core.allocation.signals import allocation_expire
from coldfront.core.project.models import ProjectUser
from coldfront.core.user.models import User
from coldfront.core.utils.common import import_from_settings
from coldfront.core.utils.mail import send_email_template

# Get an instance of a logger
logger = logging.getLogger(__name__)


CENTER_NAME = import_from_settings("CENTER_NAME")
CENTER_BASE_URL = import_from_settings("CENTER_BASE_URL")
CENTER_PROJECT_RENEWAL_HELP_URL = import_from_settings("CENTER_PROJECT_RENEWAL_HELP_URL")
EMAIL_SENDER = import_from_settings("EMAIL_SENDER")
EMAIL_OPT_OUT_INSTRUCTION_URL = import_from_settings("EMAIL_OPT_OUT_INSTRUCTION_URL")
EMAIL_SIGNATURE = import_from_settings("EMAIL_SIGNATURE")
EMAIL_ALLOCATION_EXPIRING_NOTIFICATION_DAYS = import_from_settings(
    "EMAIL_ALLOCATION_EXPIRING_NOTIFICATION_DAYS",
    [
        7,
    ],
)

EMAIL_ADMINS_ON_ALLOCATION_EXPIRE = import_from_settings("EMAIL_ADMINS_ON_ALLOCATION_EXPIRE")
EMAIL_ADMIN_LIST = import_from_settings("EMAIL_ADMIN_LIST")

EMAIL_ALLOCATION_EULA_IGNORE_OPT_OUT = import_from_settings("EMAIL_ALLOCATION_EULA_IGNORE_OPT_OUT")


EMAIL_TICKET_SYSTEM_ADDRESS = import_from_settings("EMAIL_TICKET_SYSTEM_ADDRESS")


def update_statuses():
    expired_status_choice = AllocationStatusChoice.objects.get(name="Expired")
    allocations_to_expire = Allocation.objects.filter(
        status__name__in=[
            "Active",
            "Payment Pending",
            "Payment Requested",
            "Unpaid",
        ],
        end_date__lt=datetime.datetime.now().date(),
        project__requires_review=True,
    )
    for sub_obj in allocations_to_expire:
        sub_obj.status = expired_status_choice
        sub_obj.save()
        allocation_expire.send(sender=update_statuses, allocation_pk=sub_obj.pk)

    logger.info("Allocations set to expired: {}".format(allocations_to_expire.count()))


def send_eula_reminders():
    for allocation in Allocation.objects.all():
        if not allocation.get_eula():
            continue

        email_receivers = allocation.get_user_emails(status_name="PendingEULA")

        if not email_receivers:
            continue

        template_context = {
            "resource": allocation.get_parent_resource,
            "url": f"{CENTER_BASE_URL.strip('/')}/{'allocation'}/{allocation.pk}/review-eula",
        }

        send_email_template(
            f"Reminder: Agree to EULA for {allocation}",
            "email/allocation_eula_reminder.txt",
            template_context,
            email_receivers,
        )
        logger.debug(f"Allocation(s) EULA reminder sent to users {email_receivers}.")


def send_expiry_emails():
    users = User.objects.all().prefetch_related(
        Prefetch(
            lookup="allocationuser_set",
            queryset=AllocationUser.objects.filter(
                allocation__project__requires_review=True,
                allocation__is_locked=False,
                allocation__project__status__name="Active",
            )
            .exclude(allocation__end_date=F("allocation__project__end_date"))
            .select_related(
                "status",
                "allocation",
                "allocation__status",
                "allocation__project",
                "allocation__project__pi",
                "allocation__project__type",
            )
            .prefetch_related(
                "allocation__resources",
                "allocation__allocationattribute_set",
                "allocation__allocationattribute_set__allocation_attribute_type",
            ),
            to_attr="active_user_allocations",
        ),
        Prefetch(
            lookup="projectuser_set",
            queryset=ProjectUser.objects.filter(
                status__name="Active", project__status__name="Active", enable_notifications=True
            ).select_related("role", "user", "project"),
            to_attr="project_users",
        ),
    )
    # Allocations expiring soon
    for user in users:
        projectdict = {}
        expirationdict = {}
        email_receiver_list = []
        for days_remaining in sorted(set(EMAIL_ALLOCATION_EXPIRING_NOTIFICATION_DAYS)):
            expring_in_days = (datetime.datetime.today() + datetime.timedelta(days=days_remaining)).date()

            for allocationuser in user.active_user_allocations:
                allocation = allocationuser.allocation
                if allocation.status.name not in ["Active", "Payment Pending", "Payment Requested", "Unpaid"]:
                    continue
                if not allocation.end_date == expring_in_days:
                    continue

                project_url = f"{CENTER_BASE_URL.strip('/')}/{'project'}/{allocation.project.pk}/"

                resource_name = allocation.get_parent_resource.name
                for projectuser in user.project_users:
                    if not projectuser.user == user:
                        continue
                    if not allocation.project == projectuser.project:
                        continue
                    if not projectuser.role.name == "Manager":
                        continue

                    if user.email not in email_receiver_list:
                        email_receiver_list.append(user.email)

                    if days_remaining not in expirationdict:
                        expirationdict[days_remaining] = []
                        expirationdict[days_remaining].append(
                            (project_url, resource_name, ", ".join(allocation.get_identifiers.values()))
                        )
                    else:
                        expirationdict[days_remaining].append(
                            (project_url, resource_name, ", ".join(allocation.get_identifiers.values()))
                        )

                    if allocation.project.title not in projectdict:
                        projectdict[allocation.project.title] = (
                            project_url,
                            allocation.project.pi.username,
                            allocation.project.get_env.get("renewable"),
                            allocation.project.type.name,
                        )

        if email_receiver_list:
            template_context = {
                "center_name": CENTER_NAME,
                "project_dict": projectdict,
                "expiration_dict": expirationdict,
                "project_renewal_help_url": CENTER_PROJECT_RENEWAL_HELP_URL,
                "opt_out_instruction_url": EMAIL_OPT_OUT_INSTRUCTION_URL,
                "help_email": EMAIL_TICKET_SYSTEM_ADDRESS,
                "signature": EMAIL_SIGNATURE,
            }
            send_email_template(
                f"Your access to {CENTER_NAME} resources is expiring soon",
                "email/allocation_expiring.txt",
                template_context,
                email_receiver_list,
                EMAIL_TICKET_SYSTEM_ADDRESS,
            )

            logger.debug(f"Allocation(s) expiring email sent to user {user}.")

    # Allocations expired
    admin_projectdict = {}
    admin_allocationdict = {}
    for user in users:
        projectdict = {}
        allocationdict = {}
        email_receiver_list = []

        expring_in_days = (datetime.datetime.today() + datetime.timedelta(days=-1)).date()

        for allocationuser in user.active_user_allocations:
            allocation = allocationuser.allocation
            if allocation.status.name != "Active":
                continue
            if not allocation.end_date == expring_in_days:
                continue

            project_url = f"{CENTER_BASE_URL.strip('/')}/{'project'}/{allocation.project.pk}/"

            allocation_renew_url = f"{CENTER_BASE_URL.strip('/')}/{'allocation'}/{allocation.pk}/"

            allocation_url = f"{CENTER_BASE_URL.strip('/')}/{'allocation'}/{allocation.pk}/"

            resource_name = allocation.get_parent_resource.name
            for projectuser in user.project_users:
                if not projectuser.user == user:
                    continue
                if not allocation.project == projectuser.project:
                    continue
                if not projectuser.role.name == "Manager":
                    if allocationuser.status.name not in ["Active", "Invited", "Disabled"]:
                        continue

                if user.email not in email_receiver_list:
                    email_receiver_list.append(user.email)

                if project_url not in allocationdict:
                    allocationdict[project_url] = []
                    allocationdict[project_url].append({allocation_renew_url: resource_name})
                else:
                    if {allocation_renew_url: resource_name} not in allocationdict[project_url]:
                        allocationdict[project_url].append({allocation_renew_url: resource_name})

                if allocation.project.title not in projectdict:
                    projectdict[allocation.project.title] = (
                        project_url,
                        allocation.project.pi.username,
                        allocation.project.get_env.get("renewable"),
                        allocation.project.type.name,
                    )

                if EMAIL_ADMINS_ON_ALLOCATION_EXPIRE:
                    if project_url not in admin_allocationdict:
                        admin_allocationdict[project_url] = []
                        admin_allocationdict[project_url].append({allocation_url: resource_name})
                    else:
                        if {allocation_url: resource_name} not in admin_allocationdict[project_url]:
                            admin_allocationdict[project_url].append({allocation_url: resource_name})

                    if allocation.project.title not in admin_projectdict:
                        admin_projectdict[allocation.project.title] = (
                            project_url,
                            allocation.project.pi.username,
                        )

        if email_receiver_list:
            template_context = {
                "center_name": CENTER_NAME,
                "project_dict": projectdict,
                "allocation_dict": allocationdict,
                "project_renewal_help_url": CENTER_PROJECT_RENEWAL_HELP_URL,
                "opt_out_instruction_url": EMAIL_OPT_OUT_INSTRUCTION_URL,
                "help_email": EMAIL_TICKET_SYSTEM_ADDRESS,
                "signature": EMAIL_SIGNATURE,
            }
            send_email_template(
                f"Your access to {CENTER_NAME} resources has expired",
                "email/allocation_expired.txt",
                template_context,
                email_receiver_list,
                EMAIL_TICKET_SYSTEM_ADDRESS,
            )

            logger.debug(f"Allocation(s) expired email sent to user {user}.")

    if EMAIL_ADMINS_ON_ALLOCATION_EXPIRE:
        if admin_projectdict:
            admin_template_context = {
                "project_dict": admin_projectdict,
                "allocation_dict": admin_allocationdict,
                "signature": EMAIL_SIGNATURE,
            }

            send_email_template(
                "Allocation(s) have expired",
                "email/admin_allocation_expired.txt",
                admin_template_context,
                [EMAIL_ADMIN_LIST],
            )
