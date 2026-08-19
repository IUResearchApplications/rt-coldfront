# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import logging

from django.conf import settings
from django.contrib import messages
from django.db.models import Prefetch, Q
from django.forms.models import model_to_dict
from django.urls import reverse
from django.utils.html import format_html

from coldfront.core.allocation.models import (
    ALLOCATION_RESOURCE_ORDERING,
    AllocationAdminAction,
    AllocationUser,
    AllocationUserRoleChoice,
    AllocationUserStatusChoice,
)
from coldfront.core.resource.models import Resource
from coldfront.core.utils.common import get_domain_url, import_from_settings
from coldfront.core.utils.groups import check_if_groups_in_review_groups
from coldfront.core.utils.mail import send_email_template

logger = logging.getLogger(__name__)

EMAIL_ENABLED = import_from_settings("EMAIL_ENABLED", False)
if EMAIL_ENABLED:
    EMAIL_SENDER = import_from_settings("EMAIL_SENDER")
    EMAIL_TICKET_SYSTEM_ADDRESS = import_from_settings("EMAIL_TICKET_SYSTEM_ADDRESS")
    EMAIL_OPT_OUT_INSTRUCTION_URL = import_from_settings("EMAIL_OPT_OUT_INSTRUCTION_URL")
    EMAIL_SIGNATURE = import_from_settings("EMAIL_SIGNATURE")
    EMAIL_CENTER_NAME = import_from_settings("CENTER_NAME")
    EMAIL_RESOURCE_EMAIL_TEMPLATES = import_from_settings("EMAIL_RESOURCE_EMAIL_TEMPLATES", {})

    def parent_resources_prefetch(lookup="resources", extra_select_related=()):
        """Prefetch an allocation's resources, ordered as parent resources, with
        resource_type joined so Allocation.get_parent_resource (and Resource.__str__)
        can be rendered without an extra query per allocation.

        The prefetched list is stored on each Allocation instance as
        ``_parent_resources``, which Allocation.get_parent_resource /
        get_resources_as_string consume when present.

        ``extra_select_related`` adds further relations to join on the Resource
        queryset (e.g. ``("parent_resource",)`` for callers that traverse
        ``parent_resource.parent_resource``)."""
        return Prefetch(
            lookup,
            queryset=Resource.objects.select_related("resource_type", *extra_select_related).order_by(
                *ALLOCATION_RESOURCE_ORDERING
            ),
            to_attr="_parent_resources",
        )


# TODO - review file


def set_allocation_user_status_to_error(allocation_user_pk):
    allocation_user_obj = AllocationUser.objects.get(pk=allocation_user_pk)
    error_status = AllocationUserStatusChoice.objects.get(name="Error")
    allocation_user_obj.status = error_status
    allocation_user_obj.save()


def generate_guauge_data_from_usage(name, value, usage):
    label = "%s: %.2f of %.2f" % (name, usage, value)

    try:
        percent = (usage / value) * 100
    except ZeroDivisionError:
        percent = 100
    except ValueError:
        percent = 100

    if percent < 80:
        color = "#6da04b"
    elif percent >= 80 and percent < 90:
        color = "#ffc72c"
    else:
        color = "#e56a54"

    usage_data = {
        "columns": [
            [label, percent],
        ],
        "type": "gauge",
        "colors": {label: color},
    }

    return usage_data


def get_user_resources(user_obj):
    if user_obj.is_superuser:
        resources = Resource.objects.filter(is_allocatable=True)
    else:
        resources = Resource.objects.filter(
            Q(is_allocatable=True)
            & Q(is_available=True)
            & (
                Q(is_public=True)
                | Q(allowed_groups__in=user_obj.groups.all())
                | Q(
                    allowed_users__in=[
                        user_obj,
                    ]
                )
            )
        ).distinct()

    return resources


def test_allocation_function(allocation_pk):
    print("test_allocation_function", allocation_pk)


def send_added_user_email(request, allocation_obj, users, users_emails):
    if EMAIL_ENABLED:
        domain_url = get_domain_url(request)
        allocation_url = "{}{}".format(domain_url, reverse("allocation-detail", kwargs={"pk": allocation_obj.pk}))
        project_obj = allocation_obj.project
        project_url = "{}{}".format(domain_url, reverse("project-detail", kwargs={"pk": project_obj.pk}))
        template_context = {
            "center_name": EMAIL_CENTER_NAME,
            "resource": allocation_obj.get_parent_resource.name,
            "users": users,
            "project_title": project_obj.title,
            "allocation_url": allocation_url,
            "project_url": project_url,
            "action_user": f"{request.user.first_name} {request.user.last_name}",
            "project_pi": f"{project_obj.pi.first_name} {project_obj.pi.last_name}",
            "signature": EMAIL_SIGNATURE,
            "allocation_identifiers": allocation_obj.get_identifiers.items(),
            "allocation_status": allocation_obj.status.name,
        }

        send_email_template(
            "Added to Allocation",
            EMAIL_RESOURCE_EMAIL_TEMPLATES.get(allocation_obj.get_parent_resource.name, {}).get(
                "added_user", "email/allocation_added_users.txt"
            ),
            template_context,
            users_emails,
            EMAIL_TICKET_SYSTEM_ADDRESS,
        )


def send_removed_user_email(request, allocation_obj, users, users_emails):
    domain_url = get_domain_url(request)
    project_obj = allocation_obj.project
    project_url = "{}{}".format(domain_url, reverse("project-detail", kwargs={"pk": project_obj.pk}))
    if EMAIL_ENABLED:
        template_context = {
            "center_name": EMAIL_CENTER_NAME,
            "resource": allocation_obj.get_parent_resource.name,
            "users": users,
            "project_title": project_obj.title,
            "project_url": project_url,
            "action_user": f"{request.user.first_name} {request.user.last_name}",
            "project_pi": f"{project_obj.pi.first_name} {project_obj.pi.last_name}",
            "signature": EMAIL_SIGNATURE,
            "allocation_identifiers": allocation_obj.get_identifiers.items(),
        }

        send_email_template(
            "Removed From Allocation",
            EMAIL_RESOURCE_EMAIL_TEMPLATES.get(allocation_obj.get_parent_resource.name, {}).get(
                "removed_user", "email/allocation_removed_users.txt"
            ),
            template_context,
            users_emails,
            EMAIL_TICKET_SYSTEM_ADDRESS,
        )


def create_admin_action(user, fields_to_check, allocation, base_model=None):
    if base_model is None:
        base_model = allocation
    base_model_dict = model_to_dict(base_model)

    for key, value in fields_to_check.items():
        base_model_value = base_model_dict.get(key)
        if type(value) is not type(base_model_value):
            if key == "status":
                status_class = base_model._meta.get_field("status").remote_field.model
                base_model_value = status_class.objects.get(pk=base_model_value).name
                value = value.name
            if key == "project":
                project_class = base_model._meta.get_field("project").remote_field.model
                base_model_value = project_class.objects.get(pk=base_model_value).pk
                value = value.pk
        if value != base_model_value:
            AllocationAdminAction.objects.create(
                user=user,
                allocation=allocation,
                action=f'For "{base_model}" changed "{key}" from "{base_model_value}" to "{value}"',
            )


def create_admin_action_for_deletion(user, deleted_obj, allocation, base_model=None):
    if base_model:
        AllocationAdminAction.objects.create(
            user=user, allocation=allocation, action=f'Deleted "{deleted_obj}" from "{base_model}"'
        )
    else:
        AllocationAdminAction.objects.create(user=user, allocation=allocation, action=f'Deleted "{deleted_obj}"')


def create_admin_action_for_creation(user, created_obj, allocation, base_model=None):
    if base_model:
        AllocationAdminAction.objects.create(
            user=user,
            allocation=allocation,
            action=f'Created "{created_obj}" in "{base_model}" in "{allocation}" with value "{created_obj.value}"',
        )
    else:
        AllocationAdminAction.objects.create(
            user=user,
            allocation=allocation,
            action=f'Created "{created_obj}" in "{allocation}" with value "{created_obj.value}"',
        )


def create_admin_action_for_allocation_creation(user, allocation):
    AllocationAdminAction.objects.create(
        user=user,
        allocation=allocation,
        action=f'Created a {allocation.get_parent_resource.name} allocation with status "{allocation.status.name}"',
    )


def get_allocation_user_emails(allocation_obj, only_project_managers=False):
    """
    Returns a list of allocation user emails in the given allocation. Only emails from users with
    their notifications enabled will be returned.

    :param allocation_obj: The allocation to grab the allocation user emails from
    :param only_project_managers: Indicates if only the project manager emails should be returned
    """
    allocation_users = allocation_obj.allocationuser_set.filter(
        status__name__in=[
            "Active",
        ]
    ).values_list("user", flat=True)
    allocation_users = allocation_obj.project.projectuser_set.filter(
        enable_notifications=True, user__in=list(allocation_users)
    )
    if only_project_managers:
        allocation_users = allocation_users.filter(role__name="Manager")
    allocation_users = allocation_users.values_list("user__email", flat=True)

    return list(allocation_users)


def check_if_roles_are_enabled(allocation_obj):
    return allocation_obj.get_parent_resource.requires_user_roles


def user_in_review_group_with_perm(user, allocation_obj, permission=None):
    """
    Return True if the user is a superuser or belongs to a review group of the
    allocation's parent resource that has the given permission.
    """
    if user.is_superuser:
        return True
    return check_if_groups_in_review_groups(
        allocation_obj.get_parent_resource.review_groups.all(),
        user.groups.all(),
        permission,
    )


def user_can_move_allocation(user, allocation_obj):
    """
    Return True if the movable allocations plugin is enabled and the user is
    allowed to move the allocation (superuser or a review group with the
    "can_move_allocations" permission).
    """
    if "coldfront.plugins.movable_allocations" not in settings.INSTALLED_APPS:
        return False
    return user_in_review_group_with_perm(user, allocation_obj, "can_move_allocations")


def get_default_allocation_user_role(resource, project_obj, user):
    project_managers = project_obj.projectuser_set.filter(role__name="Manager").values_list("user__username", flat=True)
    is_manager = user.username in project_managers
    if resource.requires_user_roles:
        if is_manager:
            return AllocationUserRoleChoice.objects.filter(resources=resource, is_manager_default=True).first()
        else:
            return AllocationUserRoleChoice.objects.filter(resources=resource, is_user_default=True).first()

    return AllocationUserRoleChoice.objects.none()


def set_default_allocation_user_role(resource, allocation_user):
    role_choice_queryset = get_default_allocation_user_role(
        resource, allocation_user.allocation.project, allocation_user.user
    )
    if role_choice_queryset:
        allocation_user.role = role_choice_queryset
        allocation_user.save()


def validate_user_accounts_to_add(request, allocation_obj, resource, selected_users):
    """
    Check that each selected user has an account on the resource. Users without
    an account are removed from selected_users and a warning message is shown.
    """
    user_account_statuses = resource.get_user_account_statuses([username for username in selected_users.keys()])

    missing_accounts = []
    missing_resource_accounts = []
    for username, result in user_account_statuses.items():
        if not result.get("exists"):
            if result.get("reason") == "no_account":
                missing_accounts.append(username)
            elif result.get("reason") == "no_resource_account":
                missing_resource_accounts.append(username)
            selected_users.pop(username)

    if missing_accounts:
        message = "The following user does not have an IU account and was not added:"
        if len(missing_accounts) > 1:
            message = "The following users do not have IU accounts and were not added:"
        messages.warning(request, f"{message} {', '.join(missing_accounts)}")
        logger.info(
            f"User(s) {', '.join(missing_accounts)} do not have IU accounts and "
            f"were not added to a {resource.name} "
            f"allocation (allocation pk={allocation_obj.pk})"
        )

    if missing_resource_accounts:
        message = "The following user does not have an account on this resource and was not added:"
        if len(missing_resource_accounts) > 1:
            message = "The following users do not have an account on this resource and were not added:"
        accounts_url = "https://access.iu.edu/Accounts/Create"
        messages.warning(
            request,
            format_html(
                f"{message} {', '.join(missing_resource_accounts)}. Please direct them "
                f'to <a href="{accounts_url}">{accounts_url}</a> to create one.'
            ),
        )

        logger.info(
            f"User(s) {', '.join(missing_resource_accounts)} were missing accounts for a "
            f"{resource.name} allocation (allocation pk={allocation_obj.pk})"
        )


def allocation_exceeds_user_limit(request, allocation_obj, resource, selected_users):
    """
    Return True if adding the selected users would exceed the resource's
    user limit. A warning message is shown when the limit is exceeded.
    """
    allocation_user_limit = resource.get_attribute("user_limit")
    if not allocation_user_limit:
        return False

    existing_users = allocation_obj.allocationuser_set.exclude(status__name__in=["Removed"]).values_list(
        "user__username", flat=True
    )
    total_users = len(list(existing_users)) + len(selected_users)
    if total_users > int(allocation_user_limit):
        messages.warning(
            request,
            f"Only {allocation_user_limit} users are allowed on this resource. Users "
            f"were not added. (Total users counted: {total_users})",
        )
        return True
    return False


def notify_added_users(request, allocation_obj, resource, selected_users, selected_user_objs):
    """
    Send notification emails and show a success message for the users added
    to the allocation.
    """
    allocation_added_users_emails = list(
        allocation_obj.project.projectuser_set.filter(
            user__in=selected_user_objs, enable_notifications=True
        ).values_list("user__email", flat=True)
    )
    if allocation_obj.project.pi.email not in allocation_added_users_emails:
        allocation_added_users_emails.append(allocation_obj.project.pi.email)

    send_added_user_email(request, allocation_obj, selected_user_objs, allocation_added_users_emails)

    is_plural = len(selected_users.keys()) > 1
    messages.success(
        request,
        f"User{'s' if is_plural else ''} added to the allocation: {', '.join(selected_users.keys())}",
    )

    logger.info(
        f"User {request.user.username} added {', '.join(selected_users.keys())} "
        f"to a {resource.name} allocation "
        f"(allocation pk={allocation_obj.pk})"
    )


def notify_removed_users(request, allocation_obj, removed_user_objs, remove_users_count):
    """
    Send notification emails and show a success message for the users removed
    from the allocation.
    """
    removed_users = [user_obj.username for user_obj in removed_user_objs]

    allocation_removed_users_emails = list(
        allocation_obj.project.projectuser_set.filter(
            user__in=removed_user_objs, enable_notifications=True
        ).values_list("user__email", flat=True)
    )
    if allocation_obj.project.pi.email not in allocation_removed_users_emails:
        allocation_removed_users_emails.append(allocation_obj.project.pi.email)

    send_removed_user_email(request, allocation_obj, removed_user_objs, allocation_removed_users_emails)

    user_plural = "user" if remove_users_count == 1 else "users"
    messages.success(request, f"Removed {user_plural} {', '.join(removed_users)} from allocation.")

    logger.info(
        f"User {request.user.username} removed {', '.join(removed_users)} from a "
        f"{allocation_obj.get_parent_resource.name} allocation (allocation pk={allocation_obj.pk})"
    )
