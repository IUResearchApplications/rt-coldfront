# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import datetime

from django.contrib.auth.models import User

from coldfront.core.allocation.models import Allocation, AllocationAttribute
from coldfront.core.project.models import Project, ProjectUser


def generate_publication_by_year_chart_data(publications_by_year):
    if publications_by_year:
        years, publications = zip(*publications_by_year)
        years = list(years)
        publications = list(publications)
        years.insert(0, "Year")
        publications.insert(0, "Publications")

        data = {"x": "Year", "columns": [years, publications], "type": "bar", "colors": {"Publications": "#17a2b8"}}
    else:
        data = {"columns": [], "type": "bar"}

    return data


def generate_total_grants_by_agency_chart_data(total_grants_by_agency):
    grants_agency_chart_data = {"columns": total_grants_by_agency, "type": "donut"}

    return grants_agency_chart_data


def generate_resources_chart_data(allocations_count_by_resource_type):
    if allocations_count_by_resource_type:
        cluster_label = "Cluster: %d" % (allocations_count_by_resource_type.get("Cluster", 0))
        # cloud_label = "Cloud: %d" % (allocations_count_by_resource_type.get("Cloud", 0))
        # server_label = "Server: %d" % (allocations_count_by_resource_type.get("Server", 0))
        storage_label = "Storage: %d" % (allocations_count_by_resource_type.get("Storage", 0))
        service_label = "Service: %d" % (allocations_count_by_resource_type.get("Service", 0))

        resource_plot_data = {
            "columns": [
                [cluster_label, allocations_count_by_resource_type.get("Cluster", 0)],
                [storage_label, allocations_count_by_resource_type.get("Storage", 0)],
                [service_label, allocations_count_by_resource_type.get("Service", 0)],
                # [cloud_label, allocations_count_by_resource_type.get("Cloud", 0)],
                # [server_label, allocations_count_by_resource_type.get("Server", 0)]
            ],
            "type": "donut",
            "colors": {
                cluster_label: "#6da04b",
                storage_label: "#ffc72c",
                service_label: "#2f9fd0",
                # cloud_label: "#2f9fd0",
                # server_label: "#e56a54",
            },
        }
    else:
        resource_plot_data = {"type": "donut", "columns": []}

    return resource_plot_data


def generate_allocations_chart_data():
    active_count = Allocation.objects.filter(status__name="Active").count()
    new_count = Allocation.objects.filter(status__name="New").count()
    renewal_requested_count = Allocation.objects.filter(status__name="Renewal Requested").count()

    now = datetime.datetime.now()
    start_time = datetime.date(now.year - 1, 1, 1)
    expired_count = Allocation.objects.filter(status__name="Expired", end_date__gte=start_time).count()

    active_label = "Active: %d" % (active_count)
    new_label = "New: %d" % (new_count)
    renewal_requested_label = "Renewal Requested: %d" % (renewal_requested_count)
    expired_label = "Expired: %d" % (expired_count)

    allocation_chart_data = {
        "columns": [
            [active_label, active_count],
            [new_label, new_count],
            [renewal_requested_label, renewal_requested_count],
            [expired_label, expired_count],
        ],
        "type": "donut",
        "colors": {
            active_label: "#6da04b",
            new_label: "#2f9fd0",
            renewal_requested_label: "#ffc72c",
            expired_label: "#e56a54",
        },
    }

    return allocation_chart_data


def generate_project_type_chart_data():
    num_research_projects_count = Project.objects.filter(
        status__name__in=["Active", "Waiting For Admin Approval", "Review Pending", "Contacted By Admin"],
        type__name="Research",
    ).count()
    num_class_projects_count = Project.objects.filter(
        status__name__in=["Active", "Waiting For Admin Approval", "Review Pending", "Contacted By Admin"],
        type__name="Class",
    ).count()

    project_type_chart_data = [
        {"name": "Research", "total": num_research_projects_count},
        {"name": "Class", "total": num_class_projects_count},
    ]

    return project_type_chart_data


def generate_project_user_chart_data():
    project_statuses = [
        "Active",
        "Waiting For Admin Approval",
        "Review Pending",
        "Contacted By Admin",
    ]
    num_active_research_users = len(
        ProjectUser.objects.filter(
            status__name="Active", project__type__name="Research", project__status__name__in=project_statuses
        )
    )
    num_active_class_users = len(
        ProjectUser.objects.filter(
            status__name="Active", project__type__name="Class", project__status__name__in=project_statuses
        )
    )

    project_user_chart_data = [
        {"name": "Research", "total": num_active_research_users},
        {"name": "Class", "total": num_active_class_users},
    ]
    return project_user_chart_data


def generate_project_status_chart_data():
    num_active_projects = Project.objects.filter(status__name="Active").count()
    num_requested_projects = Project.objects.filter(
        status__name__in=[
            "Waiting For Admin Approval",
            "Contacted By Admin",
        ]
    ).count()
    num_renewal_projects = Project.objects.filter(status__name="Review Pending").count()

    active_projects_label = f"Active: {num_active_projects}"
    requested_projects_label = f"Waiting For Admin Approval: {num_requested_projects}"
    renewal_projects = f"Renewal Requested: {num_renewal_projects}"

    project_status_chart_data = {
        "columns": [
            [active_projects_label, num_active_projects],
            [requested_projects_label, num_requested_projects],
            [renewal_projects, num_renewal_projects],
        ],
        "type": "donut",
        "colors": {active_projects_label: "#6da04b", requested_projects_label: "#2f9fd0", renewal_projects: "#ffc72c"},
    }

    return project_status_chart_data


def generate_research_project_status_columns():
    research_projects = Project.objects.filter(type__name="Research")
    num_active_projects = research_projects.filter(status__name="Active").count()
    num_requested_projects = research_projects.filter(
        status__name__in=[
            "Waiting For Admin Approval",
            "Contacted By Admin",
        ]
    ).count()
    num_renewal_projects = research_projects.filter(status__name="Review Pending").count()

    active_projects_label = f"Active (R): {num_active_projects}"
    requested_projects_label = f"Waiting For Admin Approval (R): {num_requested_projects}"
    renewal_projects = f"Renewal Requested (R): {num_renewal_projects}"

    research_project_status_columns = {
        "columns": [
            [active_projects_label, num_active_projects],
            [requested_projects_label, num_requested_projects],
            [renewal_projects, num_renewal_projects],
        ],
        "colors": {active_projects_label: "#6da04b", requested_projects_label: "#2f9fd0", renewal_projects: "#ffc72c"},
    }

    return research_project_status_columns


def generate_class_project_status_columns():
    research_projects = Project.objects.filter(type__name="Class")
    num_active_projects = research_projects.filter(status__name="Active").count()
    num_requested_projects = research_projects.filter(
        status__name__in=[
            "Waiting For Admin Approval",
            "Contacted By Admin",
        ]
    ).count()
    num_renewal_projects = research_projects.filter(status__name="Review Pending").count()

    active_projects_label = f"Active (C): {num_active_projects}"
    requested_projects_label = f"Waiting For Admin Approval (C): {num_requested_projects}"
    renewal_projects = f"Renewal Requested (C): {num_renewal_projects}"

    class_project_status_columns = {
        "columns": [
            [active_projects_label, num_active_projects],
            [requested_projects_label, num_requested_projects],
            [renewal_projects, num_renewal_projects],
        ],
        "colors": {active_projects_label: "#6da04b", requested_projects_label: "#2f9fd0", renewal_projects: "#ffc72c"},
    }

    return class_project_status_columns


def generate_user_counts():
    project_statuses = ["Active", "Waiting For Admin Approval", "Review Pending", "Contacted By Admin"]
    num_unique_active_users = len(
        set(
            ProjectUser.objects.filter(status__name="Active", project__status__name__in=project_statuses).values_list(
                "user", flat=True
            )
        )
    )
    num_unique_active_pis = len({project.pi for project in Project.objects.filter(status__name__in=project_statuses)})

    user_counts_chart_data = [
        {"name": "Unique Active Users", "total": num_unique_active_users},
        {"name": "Unique Active PIs", "total": num_unique_active_pis},
    ]

    return user_counts_chart_data


def create_years(start, stop):
    years = {}
    for year in range(start, stop + 1):
        years[str(year)] = 0

    return years


def generate_user_timeline():
    unique_users = User.objects.all().order_by("date_joined")
    start_year = unique_users[0].date_joined.year
    stop_years = unique_users[unique_users.count() - 1].date_joined.year
    years = create_years(start_year, stop_years)
    for user in unique_users:
        date_joined = user.date_joined
        year = str(date_joined.year)
        years[year] += 1

    user_timeline_chart_data = [{"name": year, "total": total} for year, total in years.items()]

    return user_timeline_chart_data


def get_home_page_slurm_info(user):
    slurm_account_attribute_objs = AllocationAttribute.objects.filter(
        allocation__status__name__in=[
            "Active",
            "Renewal Requested",
        ],
        allocation__allocationuser__user=user,
        allocation__allocationuser__status__name="Active",
        allocation_attribute_type__name="slurm_account_name",
    ).select_related("allocation", "allocation__project")
    slurm_accounts = {}
    for slurm_account_obj in slurm_account_attribute_objs:
        if not slurm_accounts.get(slurm_account_obj.value):
            slurm_accounts[slurm_account_obj.value] = {
                "project_pk": slurm_account_obj.allocation.project.pk,
                "project_title": slurm_account_obj.allocation.project.title,
                "allocations": {},
            }
        resource = slurm_account_obj.allocation.get_parent_resource
        slurm_accounts[slurm_account_obj.value]["allocations"][slurm_account_obj.allocation.pk] = resource.name

    return slurm_accounts
