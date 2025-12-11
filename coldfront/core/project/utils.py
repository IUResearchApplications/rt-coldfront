# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later
import datetime
import logging

from django.forms.models import model_to_dict

from coldfront.core.project.models import Project, ProjectAdminAction, ProjectUserRoleChoice

logger = logging.getLogger(__name__)


def add_project_status_choices(apps, schema_editor):
    ProjectStatusChoice = apps.get_model("project", "ProjectStatusChoice")

    for choice in [
        "New",
        "Active",
        "Archived",
    ]:
        ProjectStatusChoice.objects.get_or_create(name=choice)


def add_project_user_role_choices(apps, schema_editor):
    ProjectUserRoleChoice = apps.get_model("project", "ProjectUserRoleChoice")

    for choice in [
        "User",
        "Manager",
    ]:
        ProjectUserRoleChoice.objects.get_or_create(name=choice)


def add_project_user_status_choices(apps, schema_editor):
    ProjectUserStatusChoice = apps.get_model("project", "ProjectUserStatusChoice")

    for choice in [
        "Active",
        "Pending Remove",
        "Denied",
        "Removed",
    ]:
        ProjectUserStatusChoice.objects.get_or_create(name=choice)


def generate_project_code(project_code: str, project_pk: int, padding: int = 0) -> str:
    """
    Generate a formatted project code by combining an uppercased user-defined project code,
    project primary key and requested padding value (default = 0).

    :param project_code: The base project code, set through the PROJECT_CODE configuration variable.
    :param project_pk: The primary key of the project.
    :param padding: The number of digits to pad the primary key with, set through the PROJECT_CODE_PADDING configuration variable.
    :return: A formatted project code string.
    """

    return f"{project_code.lower()}{str(project_pk).zfill(padding)}"


def determine_automated_institution_choice(project, institution_map: dict):
    """
    Determine automated institution choice for a project. Taking PI email of current project
    and comparing to domain key from institution_map. Will first try to match a domain exactly
    as provided in institution_map, if a direct match cannot be found an indirect match will be
    attempted by looking for the first occurrence of an institution domain that occurs as a substring
    in the PI's email address. This does not save changes to the database. The project object in
    memory will have the institution field modified.
    :param project: Project to add automated institution choice to.
    :param institution_map: Dictionary of institution keys, values.
    """
    email: str = project.pi.email

    try:
        _, pi_email_domain = email.split("@")
    except ValueError:
        pi_email_domain = None

    direct_institution_match = institution_map.get(pi_email_domain)

    if direct_institution_match:
        project.institution = direct_institution_match
        return direct_institution_match
    else:
        for institution_email_domain, indirect_institution_match in institution_map.items():
            if institution_email_domain in pi_email_domain:
                project.institution = indirect_institution_match
                return indirect_institution_match

    return project.institution


def get_new_end_date_from_list(raw_expire_dates, check_date=None, buffer_days=0):
    """
    Finds a new end date based on the given list of expire dates.

    :param raw_expire_dates: List of expire dates tuples
    :param check_date: Date that is checked against the list of expire dates. If None then it's
    set to today
    :param buffer_days: Number of days before the current expire date where the end date should be
    set to the next expire date
    :return: A new end date
    """
    if check_date is None:
        check_date = datetime.date.today()

    if raw_expire_dates:
        expire_dates = []
        for date in raw_expire_dates:
            actual_date = datetime.date(datetime.date.today().year, date[0], date[1])
            expire_dates.append(actual_date)
    else:
        expire_dates = [datetime.date.today() + datetime.timedelta(days=365)]

    expire_dates.sort()

    buffer_dates = [date - datetime.timedelta(days=buffer_days) for date in expire_dates]

    end_date = None
    total_dates = len(expire_dates)
    for i in range(total_dates):
        if check_date < expire_dates[i]:
            if check_date >= buffer_dates[i]:
                end_date = expire_dates[(i + 1) % total_dates]
                if (i + 1) % total_dates == 0:
                    end_date = end_date.replace(end_date.year + 1)
            else:
                end_date = expire_dates[i]
            break
        elif i == total_dates - 1:
            expire_date = expire_dates[0]
            end_date = expire_date.replace(expire_date.year + 1)

    return end_date


def create_admin_action(user, fields_to_check, project, base_model=None):
    if base_model is None:
        base_model = project
    base_model_dict = model_to_dict(base_model)

    for key, value in fields_to_check.items():
        base_model_value = base_model_dict.get(key)
        if type(value) is not type(base_model_value):
            if key == "status":
                status_class = base_model._meta.get_field("status").remote_field.model
                base_model_value = status_class.objects.get(pk=base_model_value).name
                value = value.name
        if value != base_model_value:
            if type(base_model) is Project:
                action = f'Changed "{key}" from "{base_model_value}" to "{value}"'
            else:
                action = f'For "{base_model}" changed "{key}" from "{base_model_value}" to "{value}"'
            ProjectAdminAction.objects.create(user=user, project=project, action=action)


def get_project_user_emails(project_obj, only_project_managers=False):
    """
    Returns a list of project user emails in the given project. Only emails from users with their
    notifications enabled will be returned.

    :param allocation_obj: The project to grab the project user emails from
    :param only_project_managers: Indicates if only the project manager emails should be returned
    """
    project_users = project_obj.projectuser_set.filter(
        enable_notifications=True,
        status__name__in=[
            "Active",
        ],
    )
    if only_project_managers:
        project_users = project_users.filter(role__name="Manager")
    project_users = project_users.values_list("user__email", flat=True)

    return list(project_users)


def create_admin_action_for_deletion(user, deleted_obj, project, base_model=None):
    if base_model:
        ProjectAdminAction.objects.create(
            user=user, project=project, action=f'Deleted "{deleted_obj}" from "{base_model}"'
        )
    else:
        ProjectAdminAction.objects.create(user=user, project=project, action=f'Deleted "{deleted_obj}"')


def create_admin_action_for_creation(user, created_obj, project, base_model=None):
    if base_model:
        ProjectAdminAction.objects.create(
            user=user,
            project=project,
            action=f'Created "{created_obj}" in "{base_model}" with value "{created_obj.value}"',
        )
    else:
        ProjectAdminAction.objects.create(
            user=user, project=project, action=f'Created "{created_obj}" with value "{created_obj.value}"'
        )


def create_admin_action_for_project_creation(user, project):
    ProjectAdminAction.objects.create(
        user=user, project=project, action=f'Created a project with status "{project.status.name}"'
    )


def check_if_pis_eligible(project_pi_usernames):
    return dict.fromkeys(project_pi_usernames, True)


def update_project_user_matches(matches):
    project_user_role_obj = ProjectUserRoleChoice.objects.get(name="User")
    for match in matches:
        match.update({"role": project_user_role_obj})

    return matches


def get_ineligible_pis(project_pi_usernames):
    return None
