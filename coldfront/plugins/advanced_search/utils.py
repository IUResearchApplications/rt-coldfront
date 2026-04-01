import datetime

from django.conf import settings
from django.contrib.auth.models import User
from django.db.models.query import QuerySet
from django.urls import reverse

from coldfront.core.allocation.models import (
    Allocation,
    AllocationAttribute,
    AllocationAttributeUsage,
    AllocationUser,
)
from coldfront.core.project.models import (
    Project,
    ProjectAttribute,
    ProjectAttributeUsage,
    ProjectUser,
)
from coldfront.core.resource.models import Resource
from coldfront.core.user.models import UserProfile


class BaseSearchTable:
    type = None
    attr_type = None

    def __init__(self, search_data: dict, attribute_data: list | None = None) -> None:
        self.search_data = search_data
        self.attribute_data = attribute_data or []
        self.attribute_queryset = None
        self.queryset = None
        self.columns = []
        self.rows = {}

    def get_queryset(self) -> None:
        raise NotImplementedError()

    def get_attribute_model(self) -> None:
        raise NotImplementedError()

    def get_attribute_usage_model(self) -> None:
        raise NotImplementedError()

    def get_attribute_data(self) -> dict:
        all_attributes = {}
        attribute_types = []
        for entry in self.attribute_data:
            attribute_type = entry.get("attribute__name")
            if attribute_type:
                attribute_types.append(attribute_type)

        attributes = (
            self.get_attribute_model()
            .objects.select_related(self.type, self.attr_type)
            .filter(**{f"{self.attr_type}__id__in": [attr.id for attr in attribute_types]})
        )
        for attribute in attributes:
            all_attributes.setdefault(getattr(attribute, self.type).id, []).append(attribute)

        return all_attributes

    def get_attribute_usage(self, additional_data: dict) -> dict:
        all_attribute_usages = {}
        attributes = [attribute for attributes in additional_data.values() for attribute in attributes]
        attribute_usages = (
            self.get_attribute_usage_model()
            .objects.prefetch_related(f"{self.type}_attribute")
            .filter(**{f"{self.type}_attribute__in": attributes})
        )
        for attribute_usage in attribute_usages:
            attribute = getattr(attribute_usage, f"{self.type}_attribute")
            parent_id = getattr(attribute, self.type).id
            all_attribute_usages.setdefault(parent_id, []).append(attribute_usage)

        return all_attribute_usages

    def build_columns(self) -> None:
        columns = []
        for key, value in self.search_data.items():
            if "display" in key and value:
                display_name = " ".join(key.split("__")[1:])
                display_name = " ".join(display_name.split("_"))
                field_name = key[len("display") + 2 :]
                columns.append({"display_name": display_name.title(), "field_name": field_name})

        for entry in self.attribute_data:
            attribute_type = entry.get("attribute__name")
            if attribute_type:
                display_name = attribute_type.name
                field_name = "attribute__name"
                columns.append({"display_name": display_name, "field_name": field_name, "id": attribute_type.id})

                has_usage = int(entry.get("attribute__has_usage"))
                if has_usage and int(has_usage):
                    display_name += " Usage"
                    field_name = "attribute__has_usage"
                    columns.append({"display_name": display_name, "field_name": field_name, "id": attribute_type.id})

        self.columns = columns

    def build_rows(self, *args: any) -> None:
        rows = {}
        for idx, obj in enumerate(self.queryset):
            rows[idx] = self.build_row(obj, *args)
        self.rows = rows

    def build_row(self, model_obj, *args):
        row = []
        for column in self.columns:
            attributes = column.get("field_name").split("__")

            if attributes[0] == "attribute":
                attributes = attributes[1:]
                current_attribute = self.get_attribute_value(model_obj.id, attributes[0], column, *args)
            else:
                current_attribute = model_obj
                for attribute in attributes:
                    if hasattr(current_attribute, attribute):
                        current_attribute = getattr(current_attribute, attribute)
                        continue

                    current_attribute = self.get_special_value(model_obj, attribute)

            if current_attribute is None:
                current_attribute = ""

            if type(current_attribute) in [datetime.datetime, datetime.date]:
                current_attribute = current_attribute.isoformat()

            row.append(current_attribute)

        return row

    def build_table(self) -> tuple:
        self.get_queryset()
        self.build_columns()
        if self.attribute_data:
            additional_data = self.get_attribute_data()
            additional_usage_data = self.get_attribute_usage(additional_data)
            self.build_rows(additional_data, additional_usage_data)
        else:
            self.build_rows()

        return self.rows, self.columns

    def filter_by_attribute(self, queryset: QuerySet, entry: dict) -> QuerySet:
        attribute_type = entry.get("attribute__name")
        attribute_value = entry.get("attribute__value")
        if not (attribute_type and attribute_value):
            return queryset

        return queryset.filter(
            **{
                "attribute__{self.attr_type}": attribute_type,
                "attribute__value__icontains": attribute_value,
            }
        )

    def filter_by_usage(self, queryset: QuerySet, entry: dict) -> QuerySet:
        attribute_has_usage = entry.get("attribute__has_usage")
        if attribute_has_usage is None or not int(attribute_has_usage):
            return queryset

        attribute_type = entry.get("attribute__name")
        attribute_usage = entry.get("attribute__usage")
        attribute_equality = entry.get("attribute__equality")
        if not (attribute_type and attribute_usage):
            return queryset

        queryset = queryset.filter(**{f"attribute__{self.attr_type}": attribute_type})
        attribute_usage_format = entry.get("attribute__usage_format")
        if attribute_usage_format == "whole":
            if attribute_equality == "lt":
                queryset = queryset.filter(**{f"attribute__{self.type}attributeusage__value__lt": attribute_usage})
            elif attribute_equality == "gt":
                queryset = queryset.filter(**{f"attribute__{self.type}attributeusage__value__gt": attribute_usage})
        elif attribute_usage_format == "percent":
            attribute_ids = queryset.values_list(f"attribute__{self.type}attributeusage", flat=True)
            attribute_ids = [attribute_id for attribute_id in attribute_ids if attribute_id is not None]
            attribute_usages = self.get_attribute_usage_model().objects.filter(
                **{f"{self.type}_attribute__id__in": attribute_ids}
            )
            remaining_entries = []
            for attribute_usage_result in attribute_usages:
                attribute_obj = getattr(attribute_has_usage, f"{self.type}_attribute")
                attribute_value_with_usage = float(attribute_obj.value)
                attribute_usage_value = attribute_usage_result.value

                fraction = attribute_usage_value / attribute_value_with_usage * 100
                if attribute_equality == "lt" and fraction < attribute_usage:
                    remaining_entries.append(attribute_obj.id)
                elif attribute_equality == "gt" and fraction > attribute_usage:
                    remaining_entries.append(attribute_obj.id)

            queryset = queryset.filter(**{f"{self.type}attribute__id__in": remaining_entries})

        return queryset

    def filter_by_attribute_parameters(self, queryset: QuerySet) -> QuerySet:
        for entry in self.attribute_data:
            queryset = self.filter_by_attribute(queryset, entry)
            queryset = self.filter_by_usage(queryset, entry)

        return queryset

    def get_attribute_value(self, id, current_attribute, column, additional_data, additional_usage_data):
        if current_attribute == "name":
            attributes = additional_data.get(id)
            if attributes is not None:
                for attribute in attributes:
                    # Assumes no duplicate project attribute types in list
                    attribute_type = getattr(attribute, self.attr_type)
                    if attribute_type.id == column.get("id"):
                        return attribute.value
        elif current_attribute == "has_usage":
            attribute_usages = additional_usage_data.get(id)
            if attribute_usages is not None:
                for attribute_usage in attribute_usages:
                    # Assumes no duplicate project attribute types in list
                    attribute = getattr(attribute_usage, f"{self.type}_attribute")
                    attribute_type = getattr(attribute, self.attr_type)
                    if attribute_type.id == column.get("id"):
                        return attribute_usage.value

        return ""


class ProjectTable(BaseSearchTable):
    type = "project"
    attr_type = "proj_attr_type"

    FILTER_MAP = {
        "title": lambda data: {"title__icontains": data},
        "description": lambda data: {"description__icontains": data},
        "pi__username": lambda data: {"pi__username__icontains": data},
        "requestor__username": lambda data: {"requestor__username__icontains": data},
        "status__name": lambda data: {"status__in": data},
        "type__name": lambda data: {"type__in": data},
        "user_username": lambda data: {
            "projectuser__user__username__icontains": data,
            "projectuser__status__name": "Active",
        },
        "projects_using_ai": lambda data: {
            "allocation__allocationattribute__allocation_attribute_type__name": "Has DL Workflow",
            "allocation__allocationattribute__value": "Yes",
            "allocation__status__name": "Active",
        },
        "created_after_date": lambda data: {"created__gt": data},
        "created_before_date": lambda data: {"created__lt": data},
        "end_date": lambda data: {"end_date": data},
    }

    def get_queryset(self):
        data = self.search_data
        projects = (
            Project.objects.select_related(
                "pi",
                "requestor",
                "status",
                "type",
            )
            .prefetch_related(
                "projectuser_set",
                "projectuser_set__status",
                "projectuser_set__user",
                "allocation_set",
                "allocation_set__status",
            )
            .all()
            .order_by("id")
        )

        filter_kwargs = {}
        for param, builder in self.FILTER_MAP.items():
            value = data.get(param)
            if value:
                filter_kwargs.update(builder(value))

        if filter_kwargs:
            projects = projects.filter(**filter_kwargs)

        projects = self.filter_by_attribute_parameters(projects)

        self.queryset = projects

    def get_special_value(self, obj, attribute):
        if attribute == "total_users":
            # Need to do all() or prefetch doesn't work and we end up running more queries
            all_project_users = obj.projectuser_set.all()
            filtered_project_users_count = 0
            for project_user in all_project_users:
                if project_user.status.name == "Active":
                    filtered_project_users_count += 1
            return filtered_project_users_count

        if attribute == "users":
            all_project_users = obj.projectuser_set.all()
            filtered_project_users = []
            for project_user in all_project_users:
                if project_user.status.name == "Active":
                    filtered_project_users.append(project_user.user.username)
            return ", ".join(filtered_project_users)

        if attribute == "resources":
            all_project_allocations = obj.allocation_set.all()
            resource_list = []
            for project_allocation in all_project_allocations:
                if project_allocation.status.name in ["Active", "Renewal Requested"]:
                    resource_list.append(f"{project_allocation.get_parent_resource.name} ({project_allocation.pk})")
        if attribute == "url":
            return f"{settings.CENTER_BASE_URL}{reverse('project-detail', kwargs={'pk': obj.pk})}"

        return None

    def get_attribute_model(self):
        return ProjectAttribute

    def get_attribute_usage_model(self):
        return ProjectAttributeUsage


class AllocationTable(BaseSearchTable):
    type = "allocation"
    attr_type = "allocation_attribute_type"

    def get_queryset(self):
        allocation_queryset = self.get_allocation_queryset()

        project_queryset = self.get_project_queryset()
        allocation_queryset = allocation_queryset.filter(project__in=list(project_queryset))

        resource_queryset = self.get_resource_queryset()
        allocation_queryset = allocation_queryset.filter(resources__in=list(resource_queryset))

        allocation_queryset = self.filter_by_attribute_parameters(allocation_queryset)

        self.queryset = allocation_queryset

    def get_allocation_queryset(self):
        data = self.search_data
        allocations = (
            Allocation.objects.select_related(
                "project",
                "project__pi",
                "project__requestor",
                "project__status",
                "project__type",
                "status",
            )
            .prefetch_related(
                "project__projectuser_set",
                "project__projectuser_set__status",
                "allocationuser_set",
                "allocationuser_set__status",
                "allocationuser_set__user",
                "resources",
                "resources__resource_type",
            )
            .all()
            .order_by("project__id")
        )

        if data.get("user_username"):
            allocations = allocations.filter(
                allocationuser__user__username__icontains=data.get("allocation__user_username"),
                allocationuser__status__name__in=["Active", "Invited", "Pending", "Disabled", "Retired"],
            )

        if data.get("status__name"):
            allocations = allocations.filter(status__in=data.get("allocation__status__name"))

        if data.get("created_after_date"):
            allocations = allocations.filter(created__gt=data.get("allocation__created_after_date"))
        if data.get("created_before_date"):
            allocations = allocations.filter(created__lt=data.get("allocation__created_before_date"))

        return allocations

    def get_project_queryset(self):
        data = self.search_data
        projects = (
            Project.objects.select_related(
                "pi",
                "requestor",
                "status",
                "type",
            )
            .prefetch_related(
                "projectuser_set",
                "projectuser_set__status",
                "projectuser_set__user",
            )
            .all()
            .order_by("id")
        )

        if data.get("project__title"):
            projects = projects.filter(title__icontains=data.get("project__title"))
        if data.get("project__description"):
            projects = projects.filter(description__icontains=data.get("project__description"))
        if data.get("project__pi__username"):
            projects = projects.filter(pi__username__icontains=data.get("project__pi__username"))
        if data.get("project__requestor__username"):
            projects = projects.filter(requestor__username__icontains=data.get("project__requestor__username"))
        if data.get("project__status__name"):
            projects = projects.filter(status__in=data.get("project__status__name"))
        if data.get("project__type__name"):
            projects = projects.filter(type__in=data.get("project__type__name"))
        if data.get("project__user_username"):
            projects = projects.filter(
                projectuser__user__username__icontains=data.get("project__user_username"),
                projectuser__status__name="Active",
            )
        if data.get("project__created_after_date"):
            projects = projects.filter(created__gt=data.get("project__created_after_date"))
        if data.get("project__created_before_date"):
            projects = projects.filter(created__lt=data.get("project__created_before_date"))
        if data.get("project__end_date"):
            projects = projects.filter(end_date=data.get("project__end_date"))

        return projects

    def get_resource_queryset(self):
        data = self.search_data
        resources = Resource.objects.select_related(
            "resource_type",
        ).filter(is_allocatable=True)

        if data.get("resources__name"):
            resources = resources.filter(id__in=data.get("resources__name").values_list("id"))
        if data.get("resources__resource_type__name"):
            resources = resources.filter(resource_type__in=data.get("resources__resource_type__name"))

        return resources

    def get_special_value(self, obj, attribute):
        if attribute == "project__total_users":
            # Need to do all() or prefetch doesn't work and we end up running more queries
            all_project_users = obj.projectuser_set.all()
            filtered_project_users_count = 0
            for project_user in all_project_users:
                if project_user.status.name == "Active":
                    filtered_project_users_count += 1
            return filtered_project_users_count

        if attribute == "project__url":
            return f"{settings.CENTER_BASE_URL}{reverse('project-detail', kwargs={'pk': obj.pk})}"

        if attribute == "total_users":
            all_allocation_users = obj.allocationuser_set.all()
            filtered_allocation_users_count = 0
            for allocation_user in all_allocation_users:
                if allocation_user.status.name in ["Active", "Invited", "Pending", "Disabled", "Retired"]:
                    filtered_allocation_users_count += 1
            return filtered_allocation_users_count

        if attribute == "users":
            all_allocation_users = obj.allocationuser_set.all()
            filtered_allocation_users = []
            for allocation_user in all_allocation_users:
                if allocation_user.status.name in ["Active", "Invited", "Pending", "Disabled", "Retired"]:
                    filtered_allocation_users.append(allocation_user.user.username)
            return ", ".join(filtered_allocation_users)

        if attribute == "url":
            return f"{settings.CENTER_BASE_URL}{reverse('allocation-detail', kwargs={'pk': obj.pk})}"

        return None

    def get_attribute_model(self):
        return AllocationAttribute

    def get_attribute_usage_model(self):
        return AllocationAttributeUsage


class UserTable(BaseSearchTable):
    type = "user"

    def get_queryset(self):
        user_queryset = self.get_user_queryset()

        user_profile_queryset = self.get_user_profile_queryset()
        user_queryset = user_queryset.filter(userprofile__in=user_profile_queryset)

        self.queryset = user_queryset

    def get_user_queryset(self):
        data = self.search_data
        users = User.objects.select_related("userprofile")
        if data.get("type") == "project":
            project_usernames = set(
                ProjectUser.objects.filter(status__name="Active", project__status__name="Active").values_list(
                    "user__username", flat=True
                )
            )
            users = users.filter(username__in=project_usernames)
        elif data.get("type") == "allocation":
            allocation_usernames = set(
                AllocationUser.objects.filter(
                    status__name__in=["Active", "Invited", "Pending", "Disabled", "Retired"],
                    allocation__status__name="Active",
                    allocation__project__status__name="Active",
                ).values_list("user__username", flat=True)
            )
            users = users.filter(username__in=allocation_usernames)

        if data.get("usernames"):
            usernames = data.get("usernames").split(",")
            usernames = [username.strip() for username in usernames]
            users = users.filter(username__in=usernames)
        if data.get("first_name"):
            users = users.filter(first_name=data.get("first_name"))
        if data.get("last_name"):
            users = users.filter(last_name=data.get("last_name"))

        return users

    def get_user_profile_queryset(self):
        data = self.search_data
        user_profiles = UserProfile.objects.all()

        if data.get("userprofile__title"):
            user_profiles = user_profiles.filter(title__icontains=data.get("user__userprofile__title"))
        if data.get("userprofile__department"):
            user_profiles = user_profiles.filter(department__icontains=data.get("user__userprofile__department"))

        return user_profiles

    def get_special_value(self, obj, attribute):
        if attribute == "total_projects":
            return ProjectUser.objects.filter(user=obj, status__name="Active", project__status__name="Active").count()
        if attribute == "total_pi_projects":
            return ProjectUser.objects.filter(
                user=obj, project__pi=obj, status__name="Active", project__status__name="Active"
            ).count()
        if attribute == "total_manager_projects":
            return ProjectUser.objects.filter(
                user=obj, role__name="Manager", status__name="Active", project__status__name="Active"
            ).count()
        if attribute == "total_allocations":
            return AllocationUser.objects.filter(
                user=obj,
                status__name__in=["Active", "Invited", "Pending", "Disabled", "Retired"],
                allocation__status__name="Active",
                allocation__project__status__name="Active",
            ).count()

        return None
