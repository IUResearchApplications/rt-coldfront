# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from django.db.models import Q
from django.urls import reverse
from django.forms.models import model_to_dict

from coldfront.core.allocation.models import (AllocationUser,
                                              AllocationUserStatusChoice,
                                              AllocationAdminAction,
                                              AllocationUserRoleChoice)
from coldfront.core.resource.models import Resource
from coldfront.core.utils.common import get_domain_url, import_from_settings
from coldfront.core.utils.mail import send_email_template

EMAIL_ENABLED = import_from_settings('EMAIL_ENABLED', False)
if EMAIL_ENABLED:
    EMAIL_SENDER = import_from_settings('EMAIL_SENDER')
    EMAIL_TICKET_SYSTEM_ADDRESS = import_from_settings(
        'EMAIL_TICKET_SYSTEM_ADDRESS')
    EMAIL_OPT_OUT_INSTRUCTION_URL = import_from_settings(
        'EMAIL_OPT_OUT_INSTRUCTION_URL')
    EMAIL_SIGNATURE = import_from_settings('EMAIL_SIGNATURE')
    EMAIL_CENTER_NAME = import_from_settings('CENTER_NAME')
    EMAIL_RESOURCE_EMAIL_TEMPLATES = import_from_settings('EMAIL_RESOURCE_EMAIL_TEMPLATES', {})


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
        allocation_url = '{}{}'.format(domain_url, reverse('allocation-detail', kwargs={'pk': allocation_obj.pk}))
        project_obj = allocation_obj.project
        project_url = '{}{}'.format(domain_url, reverse('project-detail', kwargs={'pk': project_obj.pk}))
        template_context = {
            'center_name': EMAIL_CENTER_NAME,
            'resource': allocation_obj.get_parent_resource.name,
            'users': users,
            'project_title': project_obj.title,
            'allocation_url': allocation_url,
            'project_url': project_url,
            'action_user': f'{request.user.first_name} {request.user.last_name}',
            'project_pi': f'{project_obj.pi.first_name} {project_obj.pi.last_name}',
            'signature': EMAIL_SIGNATURE,
            'allocation_identifiers': allocation_obj.get_identifiers().items(),
            'allocation_status': allocation_obj.status.name
        }

        send_email_template(
            'Added to Allocation',
            EMAIL_RESOURCE_EMAIL_TEMPLATES.get(
                allocation_obj.get_parent_resource.name, {}
            ).get('added_user', 'email/allocation_added_users.txt'),
            template_context,
            EMAIL_TICKET_SYSTEM_ADDRESS,
            users_emails
        )


def send_removed_user_email(request, allocation_obj, users, users_emails):
    domain_url = get_domain_url(request)
    project_obj = allocation_obj.project
    project_url = '{}{}'.format(domain_url, reverse('project-detail', kwargs={'pk': project_obj.pk}))
    if EMAIL_ENABLED:
        template_context = {
            'center_name': EMAIL_CENTER_NAME,
            'resource': allocation_obj.get_parent_resource.name,
            'users': users,
            'project_title': project_obj.title,
            'project_url': project_url,
            'action_user': f'{request.user.first_name} {request.user.last_name}',
            'project_pi': f'{project_obj.pi.first_name} {project_obj.pi.last_name}',
            'signature': EMAIL_SIGNATURE,
            'allocation_identifiers': allocation_obj.get_identifiers().items()
        }

        send_email_template(
            'Removed From Allocation',
            EMAIL_RESOURCE_EMAIL_TEMPLATES.get(
                allocation_obj.get_parent_resource.name, {}
            ).get('removed_user', 'email/allocation_removed_users.txt'),
            template_context,
            EMAIL_TICKET_SYSTEM_ADDRESS,
            users_emails
        )


def create_admin_action(user, fields_to_check, allocation, base_model=None):
    if base_model is None:
        base_model = allocation
    base_model_dict = model_to_dict(base_model)

    for key, value in fields_to_check.items():
        base_model_value = base_model_dict.get(key)
        if type(value) is not type(base_model_value):
            if key == 'status':
                status_class = base_model._meta.get_field('status').remote_field.model
                base_model_value = status_class.objects.get(pk=base_model_value).name
                value = value.name
            if key == 'project':
                project_class = base_model._meta.get_field('project').remote_field.model
                base_model_value = project_class.objects.get(pk=base_model_value).pk
                value = value.pk
        if value != base_model_value:
            AllocationAdminAction.objects.create(
                user=user,
                allocation=allocation,
                action=f'For "{base_model}" changed "{key}" from "{base_model_value}" to "{value}"'
            )


def create_admin_action_for_deletion(user, deleted_obj, allocation, base_model=None):
    if base_model:
        AllocationAdminAction.objects.create(
            user=user,
            allocation=allocation,
            action=f'Deleted "{deleted_obj}" from "{base_model}"'
        )
    else:
        AllocationAdminAction.objects.create(
            user=user,
            allocation=allocation,
            action=f'Deleted "{deleted_obj}"'
        )


def create_admin_action_for_creation(user, created_obj, allocation, base_model=None):
    if base_model:
        AllocationAdminAction.objects.create(
            user=user,
            allocation=allocation,
            action=f'Created "{created_obj}" in "{base_model}" in "{allocation}" with value "{created_obj.value}"'
        )
    else:
        AllocationAdminAction.objects.create(
            user=user,
            allocation=allocation,
            action=f'Created "{created_obj}" in "{allocation}" with value "{created_obj.value}"'
        )


def create_admin_action_for_allocation_creation(user, allocation):
    AllocationAdminAction.objects.create(
        user=user,
        allocation=allocation,
        action=f'Created a {allocation.get_parent_resource.name} allocation with status "{allocation.status.name}"'
    )


def get_allocation_user_emails(allocation_obj, only_project_managers=False):
    """
    Returns a list of allocation user emails in the given allocation. Only emails from users with
    their notifications enabled will be returned.

    :param allocation_obj: The allocation to grab the allocation user emails from
    :param only_project_managers: Indicates if only the project manager emails should be returned
    """
    allocation_users = allocation_obj.allocationuser_set.filter(
        status__name__in=['Active', 'Pending - Remove']
    ).values_list('user', flat=True)
    allocation_users = allocation_obj.project.projectuser_set.filter(
        enable_notifications=True, user__in=list(allocation_users)
    )
    if only_project_managers:
        allocation_users = allocation_users.filter(role__name='Manager')
    allocation_users = allocation_users.values_list('user__email', flat=True)

    return list(allocation_users)


def check_if_roles_are_enabled(allocation_obj):
    return allocation_obj.get_parent_resource.requires_user_roles


def get_default_allocation_user_role(resource, project_obj, user):
    project_managers = project_obj.projectuser_set.filter(
        role__name='Manager'
    ).values_list('user__username', flat=True)
    is_manager = user.username in project_managers
    if resource.requires_user_roles:
        if is_manager:
            return AllocationUserRoleChoice.objects.filter(
                resources=resource, is_manager_default=True
            ).first()
        else:
            return AllocationUserRoleChoice.objects.filter(
                resources=resource, is_user_default=True
            ).first()
        
    return AllocationUserRoleChoice.objects.none()

def set_default_allocation_user_role(resource, allocation_user):
    role_choice_queryset = get_default_allocation_user_role(
        resource, allocation_user.allocation.project, allocation_user.user
    )
    if role_choice_queryset:
        allocation_user.role = role_choice_queryset
        allocation_user.save()
