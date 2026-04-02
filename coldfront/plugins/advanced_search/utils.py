import datetime
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import F, FloatField, QuerySet
from django.db.models.expressions import ExpressionWrapper
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


class SearchFilterBuilder:
    """
    Centralized filter logic builder for different entity types.

    This class encapsulates the filter mapping logic to avoid duplication
    across table classes and make it easier to extend with new filter types.

    Example:
        filter_kwargs = SearchFilterBuilder.build_filters(
            search_data,
            table_type='project'
        )
    """

    FILTER_MAPS: Dict[str, Dict[str, callable]] = {
        "project": {
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
        },
        "allocation": {
            "user_username": lambda data: {
                "allocationuser__user__username__icontains": data,
                "allocationuser__status__name__in": ["Active", "Invited", "Pending", "Disabled", "Retired"],
            },
            "status__name": lambda data: {"status__in": data},
            "created_after_date": lambda data: {"created__gt": data},
            "created_before_date": lambda data: {"created__lt": data},
        },
        "resources": {
            "resources__name": lambda data: {"id__in": data},
            "resources__resource_type__name": lambda data: {"resource_type__in": data},
        },
        "user": {
            "first_name": lambda data: {"first_name": data},
            "last_name": lambda data: {"last_name": data},
        },
        "userprofile": {
            "department": lambda data: {"title__icontains": data},
            "title": lambda data: {"department__icontains": data},
        },
    }

    @classmethod
    def build_filters(cls, search_data: Dict[str, Any], table_type: str) -> Dict[str, Any]:
        """
        Build filter kwargs dictionary from search data.

        Args:
            search_data: Dictionary containing search parameters
            table_type: The type of table

        Returns:
            Dictionary of filter kwargs suitable for queryset.filter(**kwargs)
        """
        filter_kwargs: Dict[str, Any] = {}
        filter_map = cls.FILTER_MAPS.get(table_type, {})

        for param, builder in filter_map.items():
            value = search_data.get(param)
            if value:
                filter_kwargs.update(builder(value))

        return filter_kwargs


class BaseSearchTable:
    """
    Abstract base class for building search tables.

    This class provides the foundational structure for querying and filtering
    data across different entity types with support for custom attributes and
    usage tracking.

    Subclasses must implement:
        - get_queryset(): Define the base queryset for the entity type
        - get_attribute_model(): Return the attribute model class
        - get_attribute_usage_model(): Return the usage model class
        - get_special_value(): Handle computed fields specific to the entity

    Key Features:
        - Dynamic column building based on search parameters
        - Attribute filtering with support for value and usage thresholds
        - Prefetched querysets to minimize database queries

    Example:
        class ProjectTable(BaseSearchTable):
            type = "project"
            attr_type = "proj_attr_type"

            def get_queryset(self):
                # ... implementation
    """

    # Class attributes to be overridden by subclasses
    type: Optional[str] = None
    attr_type: Optional[str] = None

    ATTRIBUTE_FIELD_MAP_FOR_USAGE: Dict[str, str] = {
        "project": "projectattribute",
        "allocation": "allocationattribute",
        "user": "userattribute",
    }

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

        if not attribute_types:
            return all_attributes

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
        if not attributes:
            return all_attribute_usages

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
                current_attribute = self.get_nested_attribute_value(model_obj, attributes)

            if current_attribute is None:
                current_attribute = ""

            if isinstance(current_attribute, (datetime.datetime, datetime.date)):
                current_attribute = current_attribute.isoformat()

            row.append(current_attribute)

        return row

    def get_nested_attribute_value(self, model_obj, attributes):
        """Navigate through object attributes, falling back to special values."""
        current_attribute = model_obj
        for attribute in attributes:
            if hasattr(current_attribute, attribute):
                current_attribute = getattr(current_attribute, attribute)
                continue
            return self.get_special_value(model_obj, attribute)
        return current_attribute

    def build_table(self) -> Tuple[Dict[int, List[Any]], List[Dict[str, Any]]]:
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
                f"attribute__{self.attr_type}": attribute_type,
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
            queryset = self.filter_by_usage_percent(queryset, attribute_equality, attribute_usage)

        return queryset

    def filter_by_usage_percent(self, queryset: QuerySet, attribute_equality: str, attribute_usage: float) -> QuerySet:
        """
        Filter by usage percentage for an attribute.

        Args:
            queryset: The base queryset to filter
            attribute_equality: The comparison operator ('lt' or 'gt')
            attribute_usage: The usage threshold value as a percentage

        Returns:
            The filtered queryset with usage percentage applied
        """
        if not self.type or self.type not in self.ATTRIBUTE_FIELD_MAP_FOR_USAGE:
            return queryset

        attribute_field = self.ATTRIBUTE_FIELD_MAP_FOR_USAGE[self.type]
        usage_field = f"{attribute_field}usage"

        annotated_queryset = queryset.annotate(
            usage_fraction=ExpressionWrapper(
                F(f"{attribute_field}__{usage_field}__value") / F(f"{attribute_field}__value") * 100,
                output_field=FloatField(),
            )
        )

        if attribute_equality == "lt":
            annotated_queryset = annotated_queryset.filter(usage_fraction__lt=attribute_usage).exclude(
                **{f"{attribute_field}__value": 0}
            )
        elif attribute_equality == "gt":
            annotated_queryset = annotated_queryset.filter(usage_fraction__gt=attribute_usage).exclude(
                **{f"{attribute_field}__value": 0}
            )

        return annotated_queryset

    def filter_by_attribute_parameters(self, queryset: QuerySet) -> QuerySet:
        """
        Apply all attribute filters from search data to the queryset.

        Args:
            queryset: The base queryset to filter

        Returns:
            The queryset filtered by all attribute parameters
        """
        for entry in self.attribute_data:
            queryset = self.filter_by_attribute(queryset, entry)
            queryset = self.filter_by_usage(queryset, entry)
        return queryset

    def get_attribute_value(
        self, parent_id: int, current_attribute: str, column: dict, additional_data: dict, additional_usage_data: dict
    ) -> Any:
        """
        Get the value of an attribute for a specific parent object.

        Args:
            parent_id: The ID of the parent object
            current_attribute: The attribute type to retrieve
            column: The column definition containing attribute metadata
            additional_data: Dictionary of attribute data keyed by parent ID
            additional_usage_data: Dictionary of usage data keyed by parent ID

        Returns:
            The attribute value as a string, or empty string if not found
        """
        if current_attribute == "name":
            attributes = additional_data.get(parent_id)
            if attributes is not None:
                for attribute in attributes:
                    attribute_type = getattr(attribute, self.attr_type)
                    if attribute_type.id == column.get("id"):
                        return attribute.value
        elif current_attribute == "has_usage":
            attribute_usages = additional_usage_data.get(parent_id)
            if attribute_usages is not None:
                for attribute_usage in attribute_usages:
                    attribute = getattr(attribute_usage, f"{self.type}_attribute")
                    attribute_type = getattr(attribute, self.attr_type)
                    if attribute_type.id == column.get("id"):
                        return attribute_usage.value

        return ""

    def get_total_users(self, users: QuerySet, statuses: List[str]) -> int:
        """
        Count users with status in the provided list.

        Args:
            users: Prefetched queryset of users
            statuses: List of status names to include

        Returns:
            Count of users with matching status

        Note:
            A filter isn't used because the queryset is prefetched earlier.
        """
        filtered_users_count = 0
        for user in users:
            if user.status.name in statuses:
                filtered_users_count += 1
        return filtered_users_count

    def get_user_list(self, users: QuerySet, statuses: List[str]) -> str:
        """
        Get comma-separated list of usernames with status in the provided list.

        Args:
            users: Prefetched queryset of users
            statuses: List of status names to include

        Returns:
            Comma-separated string of usernames with matching status

        Note:
            A filter isn't used because the queryset is prefetched earlier.
        """
        filtered_users = []
        for user in users:
            if user.status.name in statuses:
                filtered_users.append(user.user.username)
        return ", ".join(filtered_users)

    def get_model_list(self, allocations: QuerySet) -> List[str]:
        """
        Get list of resource strings for allocations.

        Args:
            allocations: Prefetched queryset of allocations

        Returns:
            List of resource strings in format "Resource Name (ID)"

        Note:
            A filter isn't used because the queryset is prefetched earlier.
        """
        resource_list = []
        for allocation in allocations:
            if allocation.status.name in ["Active", "Renewal Requested"]:
                resource_list.append(f"{allocation.get_parent_resource.name} ({allocation.pk})")
        return resource_list


class ProjectTable(BaseSearchTable):
    """
    Search table implementation for Project entities.

    Provides project-specific search capabilities including:
        - Standard project attributes (title, description, PI, status, etc.)
        - User membership filtering
        - AI project detection
        - Date range filtering
    """

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

    def get_queryset(self) -> None:
        """
        Build the project queryset based on search parameters.

        Applies:
            - Standard filters via FILTER_MAP
            - Attribute-based filters via filter_by_attribute_parameters
            - Project-specific filters
        """
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

        filter_kwargs = SearchFilterBuilder.build_filters(self.search_data, "project")
        if filter_kwargs:
            projects = projects.filter(**filter_kwargs)

        projects = self.filter_by_attribute_parameters(projects)

        self.queryset = projects

    def get_special_value(self, obj, attribute: str) -> Optional[Any]:
        """
        Get computed special values for project objects.

        Args:
            obj: The project object
            attribute: The special attribute to compute

        Returns:
            The computed value, or None if not a special attribute
        """
        if attribute == "total_users":
            return self.get_total_users(obj.projectuser_set.all(), ["Active"])

        if attribute == "users":
            return self.get_user_list(obj.projectuser_set.all(), ["Active"])

        if attribute == "resources":
            return self.get_model_list(obj.allocation_set.all())

        if attribute == "url":
            return f"{settings.CENTER_BASE_URL}{reverse('project-detail', kwargs={'pk': obj.pk})}"

        return None

    def get_attribute_model(self) -> type:
        return ProjectAttribute

    def get_attribute_usage_model(self) -> type:
        return ProjectAttributeUsage


class AllocationTable(BaseSearchTable):
    """
    Search table implementation for Allocation entities.

    Provides allocation-specific search capabilities including:
        - Project-based filtering
        - Resource-based filtering
        - User membership filtering
        - Date range filtering
    """

    type = "allocation"
    attr_type = "allocation_attribute_type"

    def get_queryset(self) -> None:
        allocation_queryset = self.get_allocation_queryset()

        project_queryset = self.get_project_queryset()
        allocation_queryset = allocation_queryset.filter(project__in=project_queryset)

        resource_queryset = self.get_resource_queryset()
        allocation_queryset = allocation_queryset.filter(resources__in=resource_queryset)

        allocation_queryset = self.filter_by_attribute_parameters(allocation_queryset)

        self.queryset = allocation_queryset

    def get_allocation_queryset(self) -> QuerySet:
        """
        Build the allocation queryset based on search parameters.

        Returns:
            Filtered allocation queryset with prefetch-related relationships
        """
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
            
        filter_kwargs = SearchFilterBuilder.build_filters(self.search_data, "allocation")
        if filter_kwargs:
            allocations = allocations.filter(**filter_kwargs)

        return allocations

    def get_project_queryset(self) -> QuerySet:
        """
        Build the project queryset for allocation filtering.

        Returns:
            Filtered project queryset with prefetch-related relationships
        """
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

        project_filters = {}
        for filter, value in self.search_data.items():
            if filter.startswith("project__"):
                project_filters[filter[len("project__") :]] = value
        filter_kwargs = SearchFilterBuilder.build_filters(project_filters, "project")
        if filter_kwargs:
            projects = projects.filter(**filter_kwargs)

        return projects

    def get_resource_queryset(self) -> QuerySet:
        """
        Build the resource queryset for allocation filtering.

        Returns:
            Filtered resource queryset for allocatable resources
        """
        resources = Resource.objects.select_related(
            "resource_type",
        ).filter(is_allocatable=True)

        resource_filters = {}
        for filter, value in self.search_data.items():
            if filter.startswith("resources__"):
                resource_filters[filter[len("resources__") :]] = value
        filter_kwargs = SearchFilterBuilder.build_filters(resource_filters, "resources")
        if filter_kwargs:
            resources = resources.filter(**filter_kwargs)

        return resources

    def get_special_value(self, obj, attribute: str) -> Optional[Any]:
        """
        Get computed special values for allocation objects.

        Args:
            obj: The allocation object
            attribute: The special attribute to compute

        Returns:
            The computed value, or None if not a special attribute
        """
        if attribute == "project__total_users":
            # Need to do all() or prefetch doesn't work and we end up running more queries
            return self.get_total_users(obj.projectuser_set.all(), ["Active"])

        if attribute == "project__url":
            return f"{settings.CENTER_BASE_URL}{reverse('project-detail', kwargs={'pk': obj.pk})}"

        if attribute == "total_users":
            status_names = ["Active", "Invited", "Pending", "Disabled", "Retired"]
            return self.get_total_users(obj.allocationuser_set.all(), status_names)

        if attribute == "users":
            status_names = ["Active", "Invited", "Pending", "Disabled", "Retired"]
            return self.get_user_list(obj.allocationuser_set.all(), status_names)

        if attribute == "url":
            return f"{settings.CENTER_BASE_URL}{reverse('allocation-detail', kwargs={'pk': obj.pk})}"

        return None

    def get_attribute_model(self) -> type:
        return AllocationAttribute

    def get_attribute_usage_model(self) -> type:
        return AllocationAttributeUsage


class UserTable(BaseSearchTable):
    """
    Search table implementation for User entities.

    Provides user-specific search capabilities including:
        - Username-based filtering
        - Profile attribute filtering
        - Project/allocation type filtering
        - User statistics computation
    """

    type = "user"
    attr_type = "user_attr_type"

    def get_queryset(self) -> None:
        user_queryset = self.get_user_queryset()

        user_profile_queryset = self.get_user_profile_queryset()
        user_queryset = user_queryset.filter(userprofile__in=user_profile_queryset)

        self.queryset = user_queryset

    def get_user_queryset(self) -> QuerySet:
        """
        Build the user queryset based on search parameters.

        Returns:
            Filtered user queryset with prefetch-related user profile
        """
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
            
        filter_kwargs = SearchFilterBuilder.build_filters(self.search_data, "user")
        if filter_kwargs:
            users = users.filter(**filter_kwargs)

        return users

    def get_user_profile_queryset(self) -> QuerySet:
        """
        Build the user profile queryset for user filtering.

        Returns:
            Filtered user profile queryset
        """
        user_profiles = UserProfile.objects.all()
            
        user_profile_filters = {}
        for filter, value in self.search_data.items():
            if filter.startswith("userprofile__"):
                user_profile_filters[filter[len("userprofile__") :]] = value
        filter_kwargs = SearchFilterBuilder.build_filters(user_profile_filters, "userprofile")
        if filter_kwargs:
            user_profiles = user_profiles.filter(**filter_kwargs)

        return user_profiles

    def get_special_value(self, obj, attribute: str) -> Optional[Any]:
        """
        Get computed special values for user objects.

        Args:
            obj: The user object
            attribute: The special attribute to compute

        Returns:
            The computed value, or None if not a special attribute
        """
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
