import datetime
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, Type

from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import CharField, Count, F, FloatField, Func, IntegerField, Model, OuterRef, Q, QuerySet, Subquery
from django.db.models.expressions import ExpressionWrapper
from django.db.models.functions import Cast, Coalesce
from django.urls import reverse

from coldfront.core.allocation.models import (
    Allocation,
    AllocationAttribute,
    AllocationAttributeType,
    AllocationAttributeUsage,
    AllocationStatusChoice,
    AllocationUser,
)
from coldfront.core.allocation.utils import parent_resources_prefetch
from coldfront.core.project.models import (
    Project,
    ProjectAttribute,
    ProjectAttributeType,
    ProjectAttributeUsage,
    ProjectStatusChoice,
    ProjectTypeChoice,
    ProjectUser,
)
from coldfront.core.resource.models import Resource, ResourceType
from coldfront.core.user.models import UserProfile

# Maps a search type to the model used to resolve each foreign-key field's pk
# into a human-readable name for display in the search criteria summary.
FK_FIELD_MODELS = {
    "project": {
        "status__name": ProjectStatusChoice,
        "type__name": ProjectTypeChoice,
        "project__status__name": ProjectStatusChoice,
        "project__type__name": ProjectTypeChoice,
        "resources__name": Resource,
        "resources__resource_type__name": ResourceType,
        "attribute__name": ProjectAttributeType,
    },
    "allocation": {
        "status__name": AllocationStatusChoice,
        "project__status__name": ProjectStatusChoice,
        "project__type__name": ProjectTypeChoice,
        "resources__name": Resource,
        "resources__resource_type__name": ResourceType,
        "attribute__name": AllocationAttributeType,
    },
}

# Maps non-FK ChoiceField field names to a {value: display_text} dict so the
# detail-view summary shows the same human-readable labels as the on-page form.
CHOICE_FIELD_DISPLAY = {
    "attribute__equality": {"lt": "<", "gt": ">"},
    "attribute__usage_format": {"whole": ".00", "percent": "%"},
    "type": {"all": "All", "project": "Project", "allocation": "Allocation"},
}


def resolve_pks(model, value):
    """Resolve a pk (str/int) or list of pks to a comma-joined string of
    human-readable model names, preserving input order. Falls back to the
    raw value(s) on any error or if no objects are found.
    """
    raw = value if isinstance(value, list) else [value]
    raw = [v for v in raw if v not in (None, "")]
    fallback = ", ".join(str(v) for v in raw) if raw else ""
    try:
        objs = {obj.pk: str(obj) for obj in model.objects.filter(pk__in=raw)}
        names = [objs[int(pk)] for pk in raw if int(pk) in objs]
        return ", ".join(names) if names else fallback
    except (ValueError, TypeError):
        return fallback


def build_structured_fields(query_data):
    """Build a flat list of {name, value} fields for display from a saved
    search's query_data.

    Mirrors the on-page form: display fields render as True/False, the
    submit button value and select_all_* UI helpers are omitted, and
    attribute usage fields (equality, usage, usage_format) plus
    has_usage itself are only surfaced when has_usage is enabled.
    Foreign-key pks are resolved to human-readable names via FK_FIELD_MODELS.
    """
    structured_fields = []
    if not query_data:
        return structured_fields

    search_type = next(iter(query_data)).split("_")[0]
    fk_models = FK_FIELD_MODELS.get(search_type, {})

    for section_key, section_values in query_data.items():
        if not isinstance(section_values, dict):
            continue

        if section_key.endswith("_attributes"):
            by_index = {}
            for field_name, field_value in section_values.items():
                parts = field_name.split("-")
                if len(parts) < 3:
                    continue
                idx = parts[1]
                attr_field = "-".join(parts[2:])
                by_index.setdefault(idx, {})[attr_field] = field_value
            for idx in sorted(by_index, key=int):
                attr = by_index[idx]
                if not attr.get("attribute__name"):
                    continue
                attr_name = attr["attribute__name"]
                if "attribute__name" in fk_models:
                    attr_name = resolve_pks(fk_models["attribute__name"], attr_name)
                structured_fields.append({"name": "attribute__name", "value": str(attr_name)})
                if attr.get("attribute__value"):
                    structured_fields.append({"name": "attribute__value", "value": str(attr["attribute__value"])})
                if str(attr.get("attribute__has_usage", "")) == "1":
                    structured_fields.append({"name": "attribute__has_usage", "value": "True"})
                    for usage_field in (
                        "attribute__equality",
                        "attribute__usage",
                        "attribute__usage_format",
                    ):
                        if attr.get(usage_field) not in (None, ""):
                            value = str(attr[usage_field])
                            if usage_field in CHOICE_FIELD_DISPLAY:
                                value = CHOICE_FIELD_DISPLAY[usage_field].get(value, value)
                            structured_fields.append({"name": usage_field, "value": value})
        else:
            for field_name, field_value in section_values.items():
                clean_name = field_name.split("-")[-1]
                if clean_name == "submit" or clean_name.startswith("select_all"):
                    continue
                if not field_value or (isinstance(field_value, list) and len(field_value) == 0):
                    continue
                if clean_name in fk_models:
                    field_value = resolve_pks(fk_models[clean_name], field_value)
                elif isinstance(field_value, list):
                    field_value = ", ".join(str(v) for v in field_value)
                if clean_name in CHOICE_FIELD_DISPLAY:
                    field_value = CHOICE_FIELD_DISPLAY[clean_name].get(str(field_value), str(field_value))
                if clean_name.startswith("display__"):
                    field_value = "True" if str(field_value) == "1" else "False"
                structured_fields.append({"name": clean_name, "value": str(field_value)})

    return structured_fields


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

    FILTER_MAPS: Dict[str, Dict[str, Any]] = {
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
            "name": lambda data: {"id__in": data},
            "resource_type__name": lambda data: {"resource_type__in": data},
        },
        "user": {
            "first_name": lambda data: {"first_name": data},
            "last_name": lambda data: {"last_name": data},
        },
        "userprofile": {
            "department": lambda data: {"department__icontains": data},
            "title": lambda data: {"title__icontains": data},
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
        filter_map = dict(cls.FILTER_MAPS.get(table_type, {}))

        for param, builder in filter_map.items():
            value = search_data.get(param)
            if value:
                filter_kwargs.update(builder(value))

        return filter_kwargs


class BaseSearchTable(ABC):
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

    def __init__(self, search_data: Dict[str, Any], attribute_data: Optional[List[Dict[str, Any]]] = None) -> None:
        """
        Initialize the BaseSearchTable with search parameters.

        Args:
            search_data: Dictionary containing search filter parameters
            attribute_data: Optional list of attribute type information for filtering
        """
        self.search_data = search_data
        self.attribute_data = attribute_data or []
        self.attribute_queryset = None
        self.queryset = None
        self.columns = []
        self.rows = {}

    @abstractmethod
    def get_queryset(self) -> QuerySet:
        """
        Build and set the queryset for the entity type.

        This method must be implemented by subclasses to define the base
        queryset for the specific entity type being searched.

        Note:
            This is an abstract method that must be implemented by subclasses.
        """
        raise NotImplementedError()

    @abstractmethod
    def get_special_value(self, obj: Model, attribute: str) -> Optional[Any]:
        """
        Return the computed special values for a queryset.

        This method must be implemented by subclasses to return the appropriate
        computed values.

        Args:
            obj: The model instance to compute special values for
            attribute: The attribute name to compute

        Note:
            This is an abstract method that must be implemented by subclasses.
        """
        raise NotImplementedError()

    @abstractmethod
    def get_attribute_model(self) -> Optional[Type[Model]]:
        """
        Return the attribute model class for the entity type.

        This method must be implemented by subclasses to return the appropriate
        attribute model class for filtering by attribute values.

        Note:
            This is an abstract method that must be implemented by subclasses.
        """
        raise NotImplementedError()

    @abstractmethod
    def get_attribute_usage_model(self) -> Optional[Type[Model]]:
        """
        Return the attribute usage model class for the entity type.

        This method must be implemented by subclasses to return the appropriate
        attribute usage model class for filtering by attribute usage values.

        Note:
            This is an abstract method that must be implemented by subclasses.
        """
        raise NotImplementedError()

    def bucket_related(self, query, filter_kwargs, key_func):
        """
        Query a model and bucket results by a parent key.

        Args:
            model_class: The model class to query
            filter_kwargs: Dict of filter kwargs for the queryset
            key_func: Callable(obj) -> parent_id for bucketing

        Returns:
            Dict mapping parent_id -> list of objects
        """
        results = {}
        for obj in query.filter(**filter_kwargs):
            results.setdefault(key_func(obj), []).append(obj)
        return results

    def get_attribute_data(self) -> Dict[int, List[Any]]:
        """
        Retrieve attribute data for the entity type.

        Queries the attribute model for all attributes of the current type.

        Returns:
            Dictionary mapping parent object IDs to lists of attribute instances.
            The dictionary is empty if no attribute types are found or if no
            attribute model is defined for this entity type.
        """
        attribute_types = [
            entry.get("attribute__name") for entry in self.attribute_data if entry.get("attribute__name")
        ]
        if not attribute_types:
            return {}

        attribute_model = self.get_attribute_model()
        if attribute_model is None:
            return {}

        related_fields = [self.type, self.attr_type]
        filter_kwargs = {
            f"{self.attr_type}__id__in": [attr.id for attr in attribute_types],
            f"{self.type}_id__in": self.queryset.values("pk"),
        }
        return self.bucket_related(
            attribute_model.objects.select_related(*related_fields),
            filter_kwargs,
            lambda obj: getattr(obj, self.type).id,
        )

    def get_attribute_usage(self, additional_data: Dict[int, List[Any]]) -> Dict[int, List[Any]]:
        """
        Retrieve attribute usage data for the entity type.

        Queries the usage model for all usage records associated with the
        attributes provided in additional_data.

        Args:
            additional_data: Dictionary mapping parent object IDs to lists of
                            attribute instances that need usage data

        Returns:
            Dictionary mapping parent object IDs to lists of usage instances.
            The dictionary is empty if no attributes have usage data.
        """
        attributes = [a for attrs in additional_data.values() for a in attrs]
        if not attributes:
            return {}

        return self.bucket_related(
            self.get_attribute_usage_model().objects,
            {f"{self.type}_attribute__in": attributes},
            lambda obj: getattr(getattr(obj, f"{self.type}_attribute"), self.type).id,
        )

    def build_columns(self) -> None:
        """
        Build the column definitions for the table.

        Iterates through search data to identify display fields and attribute
        types, creating column definitions with display names and field names.

        Returns:
            None - Columns are stored in self.columns as a list of dictionaries
            with keys: display_name, field_name, and optionally id
        """
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

                has_usage = int(entry.get("attribute__has_usage") or 0)
                if has_usage:
                    display_name += " Usage"
                    field_name = "attribute__has_usage"
                    columns.append({"display_name": display_name, "field_name": field_name, "id": attribute_type.id})

        self.columns = columns

    def build_rows(self, *args: Any) -> None:
        """
        Build the row data for the table.

        Iterates through the queryset and builds a row for each object using
        the build_row method.

        Args:
            *args: Additional data arguments passed to build_row

        Returns:
            None - Rows are stored in self.rows as a dictionary mapping row
            indices to row data lists
        """
        rows = {}
        for idx, obj in enumerate(self.queryset):
            rows[idx] = self.build_row(obj, *args)
        self.rows = rows

    def build_row(self, model_obj: Model, *args: Any) -> List[Any]:
        """
        Build a single row for the table based on the model object and columns.

        For each column definition, retrieves the appropriate value from the
        model object or its related attributes.

        Args:
            model_obj: The model instance to build the row from
            *args: Additional data arguments for attribute lookups

        Returns:
            List of cell values corresponding to the column definitions.
            Values are converted to strings or ISO format for datetime objects.
        """
        row = []
        for column in self.columns:
            attributes = column.get("field_name").split("__")

            if attributes[0] == "attribute":
                attributes = attributes[1:]
                attribute_value = self.get_attribute_value(model_obj.id, attributes[0], column, *args)
            else:
                attribute_value = self.get_nested_attribute_value(model_obj, attributes)

            if attribute_value is None:
                attribute_value = ""

            if isinstance(attribute_value, (datetime.datetime, datetime.date)):
                attribute_value = (
                    attribute_value.isoformat(sep="T")
                    if isinstance(attribute_value, datetime.datetime)
                    else attribute_value.isoformat()
                )

            row.append(attribute_value)

        return row

    def get_nested_attribute_value(self, model_obj: Model, attributes: List[str]) -> Any:
        """
        Navigate through nested object attributes to retrieve a value.

        Traverses the attribute chain on the model object, falling back to
        special value computation if an attribute is not directly accessible.

        Args:
            model_obj: The model instance to navigate
            attributes: List of attribute names representing the path to the value

        Returns:
            The value at the end of the attribute path, or the result of
            get_special_value if the path cannot be traversed directly.
        """
        current_attribute = model_obj
        for attribute in attributes:
            if hasattr(current_attribute, attribute):
                current_attribute = getattr(current_attribute, attribute)
                continue
            return self.get_special_value(model_obj, attribute)
        return current_attribute

    def build_table(self) -> Tuple[Dict[int, List[Any]], List[Dict[str, Any]]]:
        """
        Build the complete table structure including rows and columns.

        Handles attribute data and usage data if present.

        Returns:
            - Tuple containing:
                - Dictionary mapping row indices to row data lists
                - List of column definition dictionaries
        """
        self.get_queryset()
        self.build_columns()
        if self.attribute_data:
            additional_data = self.get_attribute_data()
            additional_usage_data = self.get_attribute_usage(additional_data)
            self.build_rows(additional_data, additional_usage_data)
        else:
            self.build_rows()

        return self.rows, self.columns

    def filter_by_attribute(self, queryset: QuerySet, entry: Dict[str, Any]) -> QuerySet:
        """
        Apply attribute value filtering to the queryset.

        Filters the queryset to include only objects that have attributes
        matching the specified type and value pattern.

        Args:
            queryset: The base queryset to filter
            entry: Dictionary containing filter parameters

        Returns:
            The filtered queryset with attribute value criteria applied.
            Returns the original queryset if required parameters are missing.
        """
        attribute_type = entry.get("attribute__name")
        attribute_value = entry.get("attribute__value")
        if not (attribute_type and attribute_value):
            return queryset

        return queryset.filter(
            **{
                f"{self.type}attribute__{self.attr_type}": attribute_type,
                f"{self.type}attribute__value__icontains": attribute_value,
            }
        ).distinct()

    def filter_by_usage(self, queryset: QuerySet, entry: Dict[str, Any]) -> QuerySet:
        """
        Apply attribute usage filtering to the queryset.

        Filters the queryset based on usage thresholds for attributes.
        Supports both absolute value and percentage-based comparisons.

        Args:
            queryset: The base queryset to filter
            entry: Dictionary containing filter parameters

        Returns:
            The filtered queryset with usage criteria applied.
            Returns the original queryset if required parameters are missing.
        """
        attribute_has_usage = entry.get("attribute__has_usage")
        if not attribute_has_usage or not int(attribute_has_usage):
            return queryset

        attribute_type = entry.get("attribute__name")
        attribute_usage = entry.get("attribute__usage")
        attribute_equality = entry.get("attribute__equality")
        if not (attribute_type and attribute_usage):
            return queryset

        queryset = queryset.filter(**{f"{self.type}attribute__{self.attr_type}": attribute_type})
        attribute_usage_format = entry.get("attribute__usage_format")
        if attribute_usage_format == "whole":
            if attribute_equality == "lt":
                queryset = queryset.filter(
                    **{f"{self.type}attribute__{self.type}attributeusage__value__lt": attribute_usage}
                )
            elif attribute_equality == "gt":
                queryset = queryset.filter(
                    **{f"{self.type}attribute__{self.type}attributeusage__value__gt": attribute_usage}
                )
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
        usage_field = f"{self.type}attributeusage"

        queryset = queryset.exclude(**{f"{self.type}attribute__value": "0"})
        annotated_queryset = queryset.annotate(
            usage_fraction=ExpressionWrapper(
                F(f"{self.type}attribute__{usage_field}__value")
                / Cast(f"{self.type}attribute__value", output_field=FloatField())
                * 100,
                output_field=FloatField(),
            )
        )

        if attribute_equality == "lt":
            annotated_queryset = annotated_queryset.filter(usage_fraction__lt=attribute_usage)
        elif attribute_equality == "gt":
            annotated_queryset = annotated_queryset.filter(usage_fraction__gt=attribute_usage)

        return annotated_queryset.distinct()

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
        self,
        parent_id: int,
        current_attribute: str,
        column: Dict[str, Any],
        additional_data: Dict[int, List[Any]],
        additional_usage_data: Dict[int, List[Any]],
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

    def get_resource_list(self, allocations: QuerySet) -> str:
        """
        Get list of resource strings for allocations.

        Args:
            allocations: Prefetched queryset of allocations

        Returns:
            A string in format "Resource Name (ID), Resource Name (ID), ..."

        Note:
            A filter isn't used because the queryset is prefetched earlier.
        """
        return ", ".join(
            f"{allocation.get_parent_resource.name} ({allocation.pk})"
            for allocation in allocations
            if allocation.status.name in ["Active", "Renewal Requested"]
        )


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

    def get_queryset(self) -> None:
        """
        Build and set the project queryset with filters.

        Retrieves all projects with related data for PI, requestor, status,
        and type. Prefetches project users and allocations only when the
        corresponding display columns or filters need them. Applies search
        filters and attribute parameter filters to the queryset.

        Returns:
            None - Result is stored in self.queryset
        """
        display = self.search_data

        prefetch = []
        if display.get("display__users") or display.get("display__total_users") or display.get("user_username"):
            prefetch.extend(["projectuser_set", "projectuser_set__status", "projectuser_set__user"])

        if display.get("display__resources"):
            prefetch.extend(["allocation_set", "allocation_set__status"])
            prefetch.append(parent_resources_prefetch("allocation_set__resources"))

        projects = (
            Project.objects.select_related("pi", "requestor", "status", "type")
            .prefetch_related(*prefetch)
            .all()
            .order_by("id")
        )

        annotations = {}
        if display.get("display__total_users"):
            annotations["total_users"] = Count(
                "projectuser",
                filter=Q(projectuser__status__name="Active"),
                distinct=True,
            )
        if display.get("display__users"):
            annotations["users"] = Subquery(
                ProjectUser.objects.filter(
                    project=OuterRef("pk"),
                    status__name="Active",
                )
                .values("project")
                .annotate(
                    usernames=Func(
                        F("user__username"),
                        function="GROUP_CONCAT",
                        template="%(function)s(DISTINCT %(expressions)s ORDER BY %(expressions)s SEPARATOR ', ')",
                        output_field=CharField(),
                    )
                )
                .values("usernames")[:1]
            )

        if annotations:
            projects = projects.annotate(**annotations)

        filter_kwargs = SearchFilterBuilder.build_filters(self.search_data, "project")
        if filter_kwargs:
            projects = projects.filter(**filter_kwargs)

        projects = self.filter_by_attribute_parameters(projects)
        projects = projects.distinct()

        self.queryset = projects

    def get_special_value(self, obj: Model, attribute: str) -> Optional[Any]:
        """
        Get computed special values for project objects.

        Args:
            obj: The project object
            attribute: The special attribute to compute

        Returns:
            The computed value, or None if not a special attribute
        """
        if attribute == "resources":
            return self.get_resource_list(obj.allocation_set.all())

        if attribute == "url":
            return f"{settings.CENTER_BASE_URL}{reverse('project-detail', kwargs={'pk': obj.pk})}"

        return None

    def get_attribute_model(self) -> ProjectAttribute:
        return ProjectAttribute

    def get_attribute_usage_model(self) -> ProjectAttributeUsage:
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
        """
        Build and set the allocation queryset with filters.

        Combines allocation, project, and resource querysets to filter
        allocations based on search criteria. Applies attribute parameter
        filters to the final queryset.

        Returns:
            None - Result is stored in self.queryset
        """
        allocation_queryset = self.get_allocation_queryset()

        project_queryset = self.get_project_queryset()
        allocation_queryset = allocation_queryset.filter(project__in=project_queryset)

        resource_queryset = self.get_resource_queryset()
        allocation_queryset = allocation_queryset.filter(resources__in=resource_queryset)

        allocation_queryset = self.filter_by_attribute_parameters(allocation_queryset)
        allocation_queryset = allocation_queryset.distinct()

        self.queryset = allocation_queryset

    def get_allocation_queryset(self) -> QuerySet:
        """
        Build the base allocation queryset with search filters.

        Retrieves all allocations with related project and resource data.
        Prefetches users and resources only when the corresponding display
        columns or filters need them. Applies allocation-specific search filters.

        Returns:
            QuerySet of Allocation objects filtered by allocation search criteria.
        """
        display = self.search_data

        prefetch = [parent_resources_prefetch()]
        if (
            display.get("display__users")
            or display.get("display__total_users")
            or display.get("display__project__project_total_users")
            or display.get("user_username")
        ):
            prefetch.extend(["allocationuser_set", "allocationuser_set__status", "allocationuser_set__user"])

        if display.get("display__project__project_total_users"):
            prefetch.extend(
                ["project__projectuser_set", "project__projectuser_set__status", "project__projectuser_set__user"]
            )

        allocations = (
            Allocation.objects.select_related(
                "project", "project__pi", "project__requestor", "project__status", "project__type", "status"
            )
            .prefetch_related(*prefetch)
            .all()
            .order_by("project__id")
        )

        annotations = {}
        if display.get("display__total_users"):
            annotations["total_users"] = Count(
                "allocationuser",
                filter=Q(allocationuser__status__name__in=["Active", "Invited", "Pending", "Disabled", "Retired"]),
                distinct=True,
            )
        if display.get("display__users"):
            annotations["users"] = Subquery(
                AllocationUser.objects.filter(
                    allocation=OuterRef("pk"),
                    status__name__in=["Active", "Invited", "Pending", "Disabled", "Retired"],
                )
                .values("allocation")
                .annotate(
                    usernames=Func(
                        F("user__username"),
                        function="GROUP_CONCAT",
                        template="%(function)s(DISTINCT %(expressions)s ORDER BY %(expressions)s SEPARATOR ', ')",
                        output_field=CharField(),
                    )
                )
                .values("usernames")[:1]
            )
        if display.get("display__project__project_total_users"):
            annotations["project_total_users"] = Count(
                "project__projectuser",
                filter=Q(project__projectuser__status__name="Active"),
                distinct=True,
            )

        if annotations:
            allocations = allocations.annotate(**annotations)

        filter_kwargs = SearchFilterBuilder.build_filters(self.search_data, "allocation")
        if filter_kwargs:
            allocations = allocations.filter(**filter_kwargs)

        return allocations

    def get_project_queryset(self) -> QuerySet:
        """
        Build the project queryset for allocation filtering.

        Retrieves projects filtered by project-related search parameters
        (e.g., project__title, project__pi__username) and applies project
        filters to enable cross-entity filtering.

        Returns:
            QuerySet of Project objects filtered by project search criteria.
        """
        projects = (
            Project.objects.select_related(
                "pi",
                "requestor",
                "status",
                "type",
            )
            .all()
            .order_by("id")
        )

        if any(key.startswith("project__user") for key in self.search_data):
            projects = projects.prefetch_related(
                "projectuser_set",
                "projectuser_set__status",
                "projectuser_set__user",
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

        Retrieves allocatable resources filtered by resource-related search
        parameters (e.g., resources__name, resources__resource_type__name).

        Returns:
            QuerySet of Resource objects filtered by resource search criteria,
            containing only resources where is_allocatable=True.
        """
        resources = Resource.objects.select_related("resource_type").filter(is_allocatable=True)

        resource_filters = {}
        for filter, value in self.search_data.items():
            if filter.startswith("resources__"):
                resource_filters[filter[len("resources__") :]] = value
        filter_kwargs = SearchFilterBuilder.build_filters(resource_filters, "resources")
        if filter_kwargs:
            resources = resources.filter(**filter_kwargs)

        return resources

    def get_special_value(self, obj: Model, attribute: str) -> Optional[Any]:
        """
        Get computed special values for allocation objects.

        Args:
            obj: The allocation object
            attribute: The special attribute to compute

        Returns:
            The computed value, or None if not a special attribute
        """
        if attribute == "project_total_users":
            count = getattr(obj, "project_total_users", None)
            return count if count is not None else 0

        if attribute == "project_url":
            return f"{settings.CENTER_BASE_URL}{reverse('project-detail', kwargs={'pk': obj.project_id})}"

        if attribute == "total_users":
            count = getattr(obj, "total_users", None)
            return count if count is not None else 0

        if attribute == "users":
            usernames = getattr(obj, "users", None)
            return usernames if usernames else ""

        if attribute == "url":
            return f"{settings.CENTER_BASE_URL}{reverse('allocation-detail', kwargs={'pk': obj.pk})}"

        return None

    def get_attribute_model(self) -> AllocationAttribute:
        return AllocationAttribute

    def get_attribute_usage_model(self) -> AllocationAttributeUsage:
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
        """
        Build and set the user queryset with filters.

        Combines user and user profile querysets to filter users based on
        search criteria. Users are filtered by profile if profile search
        parameters are present.

        Returns:
            None - Result is stored in self.queryset
        """
        display = self.search_data
        user_queryset = self.get_user_queryset()

        user_profile_queryset = self.get_user_profile_queryset()
        user_queryset = user_queryset.filter(userprofile__in=user_profile_queryset)

        # Use subqueries to avoid JOIN multiplication that occurs when
        # annotating counts on a queryset that already has JOINs (e.g. the
        # userprofile filter). Each subquery computes its count independently.
        annotations = {}
        if display.get("display__total_projects"):
            annotations["total_projects"] = Coalesce(
                Subquery(
                    (
                        ProjectUser.objects.filter(
                            user=OuterRef("pk"), status__name="Active", project__status__name="Active"
                        )
                        .values("user")
                        .annotate(count=Count("id", distinct=True))
                        .values("count")
                    ),
                    output_field=IntegerField(),
                ),
                0,
            )

        if display.get("display__total_pi_projects"):
            annotations["total_pi_projects"] = Coalesce(
                Subquery(
                    (
                        ProjectUser.objects.filter(
                            user=OuterRef("pk"),
                            status__name="Active",
                            project__pi=OuterRef("pk"),
                            project__status__name="Active",
                        )
                        .values("user")
                        .annotate(count=Count("id", distinct=True))
                        .values("count")
                    ),
                    output_field=IntegerField(),
                ),
                0,
            )

        if display.get("display__total_manager_projects"):
            annotations["total_manager_projects"] = Coalesce(
                Subquery(
                    (
                        ProjectUser.objects.filter(
                            user=OuterRef("pk"),
                            role__name="Manager",
                            status__name="Active",
                            project__status__name="Active",
                        )
                        .values("user")
                        .annotate(count=Count("id", distinct=True))
                        .values("count")
                    ),
                    output_field=IntegerField(),
                ),
                0,
            )

        if display.get("display__total_allocations"):
            annotations["total_allocations"] = Coalesce(
                Subquery(
                    (
                        AllocationUser.objects.filter(
                            user=OuterRef("pk"),
                            status__name__in=["Active", "Invited", "Pending", "Disabled", "Retired"],
                            allocation__status__name="Active",
                            allocation__project__status__name="Active",
                        )
                        .values("user")
                        .annotate(count=Count("id", distinct=True))
                        .values("count")
                    ),
                    output_field=IntegerField(),
                ),
                0,
            )

        user_queryset = user_queryset.annotate(**annotations)

        self.queryset = user_queryset

    def get_user_queryset(self) -> QuerySet:
        """
        Build the base user queryset with search filters.

        Retrieves users filtered by user-specific search parameters. Can
        optionally filter users to only those with active project or
        allocation membership based on the 'type' parameter.

        Returns:
            QuerySet of User objects filtered by user search criteria.
        """
        data = self.search_data
        users = User.objects.select_related("userprofile")
        if data.get("type") == "project":
            users = users.filter(
                pk__in=ProjectUser.objects.filter(status__name="Active", project__status__name="Active").values(
                    "user__pk"
                )
            )
        elif data.get("type") == "allocation":
            users = users.filter(
                pk__in=AllocationUser.objects.filter(
                    status__name__in=["Active", "Invited", "Pending", "Disabled", "Retired"],
                    allocation__status__name="Active",
                    allocation__project__status__name="Active",
                ).values("user__pk")
            )

        if data.get("usernames"):
            usernames = [u.strip() for u in data["usernames"].split(",") if u.strip()]
            users = users.filter(username__in=usernames)

        filter_kwargs = SearchFilterBuilder.build_filters(self.search_data, "user")
        if filter_kwargs:
            users = users.filter(**filter_kwargs)

        return users

    def get_user_profile_queryset(self) -> QuerySet:
        """
        Build the user profile queryset for user filtering.

        Retrieves user profiles filtered by profile-related search parameters
        (e.g., userprofile__department, userprofile__title) and applies
        userprofile filters.

        Returns:
            QuerySet of UserProfile objects filtered by userprofile search criteria.
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

    def get_special_value(self, obj: Model, attribute: str) -> Optional[Any]:
        return None

    def get_attribute_model(self) -> Optional[Type[Model]]:
        return None

    def get_attribute_usage_model(self) -> Optional[Type[Model]]:
        return None
