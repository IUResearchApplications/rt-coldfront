import csv
import json
import logging

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.forms import formset_factory
from django.http import HttpResponseRedirect, JsonResponse
from django.http.response import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import RedirectView, TemplateView, View

from coldfront.core.allocation.models import AllocationAttributeType
from coldfront.core.project.models import ProjectAttributeType
from coldfront.core.utils.common import Echo
from coldfront.plugins.advanced_search.forms import (
    AllocationAttributeSearchForm,
    AllocationSearchForm,
    AttributeFormSetHelper,
    ProjectAttributeSearchForm,
    ProjectSearchForm,
    SearchCreateForm,
    UserSearchForm,
)
from coldfront.plugins.advanced_search.mixins import AdvancedSearchPermissionMixin, CanAccessSavedSearchMixin
from coldfront.plugins.advanced_search.models import SavedSearch
from coldfront.plugins.advanced_search.utils import AllocationTable, ProjectTable, UserTable

logger = logging.getLogger(__name__)

CACHE_TTL = 900


def add_formset_management_fields(data, prefix):
    """Add formset management fields based on the attribute data indices.

    The post() handler strips management fields (TOTAL_FORMS, etc.)
    from the session filter_data.  When a formset is later recreated from
    that data Django raises ``ManagementForm data is missing`` unless the
    fields are re-added.  This function computes them from the form indices
    actually present in data.

    Returns the computed number of forms (0 when no form fields are present).
    Management fields are only written when num_forms > 0 so that data
    from saved searches with no attributes does not force the formset to
    render zero rows instead of the extra blank row.
    """
    indices = set()
    for key in data:
        if key.startswith(f"{prefix}-"):
            parts = key.split("-", 2)
            if len(parts) >= 2 and parts[1].isdigit():
                indices.add(int(parts[1]))

    num_forms = max(indices) + 1 if indices else 0
    if num_forms > 0:
        data[f"{prefix}-TOTAL_FORMS"] = str(num_forms)
        data[f"{prefix}-INITIAL_FORMS"] = str(num_forms)
        data[f"{prefix}-MIN_NUM_FORMS"] = "0"
        data[f"{prefix}-MAX_NUM_FORMS"] = "1000"
    return num_forms


def get_usage_attribute_ids():
    """Return cached sets of attribute type IDs that have usage tracking enabled."""
    data = cache.get("advanced_search_usage_attribute_ids")
    if data is None:
        data = {
            "allocation": set(AllocationAttributeType.objects.filter(has_usage=True).values_list("id", flat=True)),
            "project": set(ProjectAttributeType.objects.filter(has_usage=True).values_list("id", flat=True)),
        }
        cache.set("advanced_search_usage_attribute_ids", data, CACHE_TTL)
    return data


def get_linked_allocation_attribute_types():
    """Return cached dict mapping resource IDs to their linked allocation attribute type options."""
    data = cache.get("advanced_search_linked_alloc_attr_types")
    if data is None:
        queryset = AllocationAttributeType.objects.prefetch_related("linked_resources")
        linked = {}
        for attr_type in queryset:
            for resource in attr_type.linked_resources.all():
                linked.setdefault(resource.id, []).append(f'<option value="{attr_type.id}">{attr_type}</option>')
        cache.set("advanced_search_linked_alloc_attr_types", linked, CACHE_TTL)
        data = linked
    return data


class AdvancedSearchView(LoginRequiredMixin, AdvancedSearchPermissionMixin, TemplateView):
    template_name = "advanced_search/advanced_search.html"

    def clean_formset_data(self, formset, usage_attribute_ids, attribute_type):
        cleaned = []
        for form in formset:
            if not form.is_valid():
                continue
            data = form.cleaned_data
            attribute_obj = data.get("attribute__name")
            if not attribute_obj or attribute_obj.id not in usage_attribute_ids:
                data["attribute__has_usage"] = "0"
            cleaned.append(data)
        return cleaned

    def handle_search(self, context, type, table_class, search_form_class):
        search_form = context.get(f"{type}_form")
        search_formset = context.get(f"{type}attribute_form", [])
        attribute_data = self.clean_formset_data(
            search_formset, get_usage_attribute_ids().get(type, []), f"{type}attribute"
        )

        if search_form is None:
            context[f"{type}_form"] = search_form_class(prefix=f"{type}_search")
        elif search_form.is_valid():
            table = table_class(search_form.cleaned_data, attribute_data)
            context["rows"], context["columns"] = table.build_table()
            context[f"{type}attribute_form"] = search_formset

    def partition_by_prefix(self, filter_data, prefix):
        """Extract filter_data entries matching a given prefix, stripping the prefix from keys."""
        return {k: v for k, v in filter_data.items() if k.startswith(prefix)}

    def build_forms(self, context, filter_data):
        """Build the three search forms from session filter data."""
        if filter_data:
            project_data = self.partition_by_prefix(filter_data, "project_search")
            allocation_data = self.partition_by_prefix(filter_data, "allocation_search")
            user_data = self.partition_by_prefix(filter_data, "user_search")
            context["project_form"] = ProjectSearchForm(
                self.normalize_form_data(project_data, ProjectSearchForm), prefix="project_search"
            )
            context["allocation_form"] = AllocationSearchForm(
                self.normalize_form_data(allocation_data, AllocationSearchForm), prefix="allocation_search"
            )
            context["user_form"] = UserSearchForm(
                self.normalize_form_data(user_data, UserSearchForm), prefix="user_search"
            )
        else:
            context["project_form"] = ProjectSearchForm(prefix="project_search")
            context["allocation_form"] = AllocationSearchForm(prefix="allocation_search")
            context["user_form"] = UserSearchForm(prefix="user_search")

    def normalize_form_data(self, data, form_class):
        """Flatten single-element lists for fields that expect a single value.

        Multi-select fields (ModelMultipleChoiceField) keep their list so the
        form correctly renders the selected option(s).
        """
        normalized = {}
        for key, value in data.items():
            field_name = key.split("-", 1)[1] if "-" in key else key
            field = form_class.base_fields.get(field_name)
            if isinstance(value, list) and len(value) == 1 and not isinstance(field, forms.ModelMultipleChoiceField):
                normalized[key] = value[0]
            else:
                normalized[key] = value
        return normalized

    def build_formsets(self, context, filter_data):
        """Build attribute formsets from session filter data."""
        project_data = (
            {
                k: v[0] if isinstance(v, list) and len(v) == 1 else v
                for k, v in filter_data.items()
                if k.startswith("projectattribute-")
            }
            if filter_data
            else {}
        )
        allocation_data = (
            {
                k: v[0] if isinstance(v, list) and len(v) == 1 else v
                for k, v in filter_data.items()
                if k.startswith("allocationattribute-")
            }
            if filter_data
            else {}
        )

        if project_data:
            add_formset_management_fields(project_data, "projectattribute")
        if allocation_data:
            add_formset_management_fields(allocation_data, "allocationattribute")

        context["projectattribute_form"] = formset_factory(ProjectAttributeSearchForm, extra=1)(
            project_data or None, prefix="projectattribute"
        )

        allocation_form = context.get("allocation_form")
        selected_resources = None
        if allocation_form and allocation_form.is_valid():
            selected_resources = allocation_form.cleaned_data.get("resources__name")

        context["allocationattribute_form"] = formset_factory(AllocationAttributeSearchForm, extra=1)(
            allocation_data or None,
            prefix="allocationattribute",
            form_kwargs={"resources": selected_resources},
        )

    def get_loaded_search_state(self):
        """Load saved search state from session, returning (search, is_owner, is_shared)."""
        loaded_search_id = self.request.session.get("loaded_search_id")
        if not loaded_search_id:
            return None, False, False

        loaded_search = SavedSearch.objects.filter(pk=loaded_search_id).first()
        if not loaded_search:
            return None, False, False

        is_owner = loaded_search.owner_id == self.request.user.pk
        is_shared = False
        if not is_owner:
            is_shared = SavedSearch.get_shared_with_user(self.request.user).filter(pk=loaded_search_id).exists()
        return loaded_search, is_owner, is_shared

    def resolve_search_type(self, submit):
        """Determine which search to run based on the submit button or session state."""
        submit_map = {
            "Project Search": "project",
            "Allocation Search": "allocation",
            "User Search": "user",
        }
        search_type = submit_map.get(submit) or self.request.session.get("search_type", "project")
        tab_map = {
            "project": "project-search",
            "allocation": "allocation-search",
            "user": "user-search",
        }
        return search_type, tab_map.get(search_type, "project-search")

    def post(self, request, *args, **kwargs):
        session = request.session
        skip_prefixes = ("csrf",)
        skip_suffixes = ("-TOTAL_FORMS", "-INITIAL_FORMS", "-MIN_NUM_FORMS", "-MAX_NUM_FORMS")
        filter_data = {
            key: value
            for key, value in request.POST.lists()
            if not key.lower().startswith(skip_prefixes) and not key.endswith(skip_suffixes) and key != "submit"
        }
        session["filter_data"] = filter_data
        session["search_type"] = request.POST.get("search_type", "project")
        submit = request.POST.get("submit", "")
        session.save()
        return HttpResponseRedirect(f"{reverse('advanced-search')}?submit={submit}")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        loaded_search, is_owner, is_shared = self.get_loaded_search_state()
        filter_data = self.request.session.get("filter_data")
        self.build_forms(context, filter_data)
        self.build_formsets(context, filter_data)

        context["rows"], context["columns"] = [], []
        context["save_search_form"] = SearchCreateForm(user=self.request.user)

        search_type, active_tab = self.resolve_search_type(self.request.GET.get("submit", ""))

        if search_type == "project":
            self.handle_search(context, "project", ProjectTable, ProjectSearchForm)
        elif search_type == "allocation":
            self.handle_search(context, "allocation", AllocationTable, AllocationSearchForm)
        elif search_type == "user":
            self.handle_search(context, "user", UserTable, UserSearchForm)

        context.update(
            {
                "allocation_attribute_type_ids": list(get_usage_attribute_ids()["allocation"]),
                "project_attribute_type_ids": list(get_usage_attribute_ids()["project"]),
                "linked_allocation_attribute_types": get_linked_allocation_attribute_types(),
                "allocationattribute_helper": AttributeFormSetHelper("allocation"),
                "projectattribute_helper": AttributeFormSetHelper("project"),
                "CENTER_BASE_URL": settings.CENTER_BASE_URL,
                "active_tab": active_tab,
                "loaded_search": loaded_search,
                "is_loaded_search_owner": is_owner,
                "loaded_search_is_shared": is_shared,
            }
        )
        return context


class ClearSearchView(LoginRequiredMixin, View):
    """View to clear the loaded search state and reset the search form."""

    def post(self, request, *args, **kwargs):
        request.session.pop("loaded_search_id", None)
        request.session.pop("loaded_search_name", None)
        request.session.pop("loaded_search_owner_id", None)
        request.session.pop("is_loaded_search_owner", None)
        request.session.pop("search_type", None)
        request.session.pop("filter_data", None)
        return HttpResponseRedirect(reverse("advanced-search"))


class SavedSearchCreateView(LoginRequiredMixin, AdvancedSearchPermissionMixin, TemplateView):
    template_name = "advanced_search/save_search_form_body.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filter_data = self.request.session.get("filter_data", {})
        search_type = self.request.session.get("search_type", "project")

        type_config = {
            "project": {"project_search": "project_search", "project_attributes": "projectattribute"},
            "allocation": {"allocation_search": "allocation_search", "allocation_attributes": "allocationattribute"},
            "user": {"user_search": "user_search"},
        }
        config = type_config.get(search_type, {})
        initial_query = {
            section: {k: v for k, v in filter_data.items() if k.startswith(f"{prefix}-")}
            for section, prefix in config.items()
        }

        context["save_search_form"] = SearchCreateForm(user=self.request.user)
        context["query_data"] = json.dumps(initial_query)
        context["search_type"] = search_type
        return context

    def post(self, request, *args, **kwargs):
        form = SearchCreateForm(request.POST, user=request.user)
        query_data_raw = request.POST.get("query_data")

        if form.is_valid():
            try:
                query_data = json.loads(query_data_raw) if query_data_raw else {}
                if not isinstance(query_data, dict):
                    raise ValueError("query_data must be a JSON object")
                # Whitelist expected keys to prevent injection of unexpected fields
                allowed_keys = {
                    "project_search",
                    "allocation_search",
                    "user_search",
                    "project_attributes",
                    "allocation_attributes",
                }
                query_data = {k: v for k, v in query_data.items() if k in allowed_keys}
                saved_search = form.save(commit=False)
                saved_search.owner = request.user
                saved_search.query_data = query_data
                saved_search.save()
                form.save_m2m()

                # Auto-load the newly created search by setting session state
                session = request.session
                session["filter_data"] = query_data
                session["loaded_search_id"] = saved_search.pk
                session["loaded_search_name"] = saved_search.name
                session["loaded_search_owner_id"] = saved_search.owner_id
                session["is_loaded_search_owner"] = True
                session.save()

                return JsonResponse({"success": True, "message": "Saved successfully.", "search_id": saved_search.pk})

            except (json.JSONDecodeError, TypeError, ValueError):
                return JsonResponse({"success": False, "message": "Invalid search data format."}, status=400)
            except Exception as e:
                logger.error(f"Error saving search: {str(e)}")
                return JsonResponse({"success": False, "message": "An unexpected error occurred."}, status=500)
        else:
            return JsonResponse(
                {"success": False, "message": "There was an issue saving your search.", "errors": form.errors},
                status=400,
            )


class SavedSearchListView(LoginRequiredMixin, AdvancedSearchPermissionMixin, TemplateView):
    template_name = "advanced_search/saved_searches.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["user_saved_searches"] = SavedSearch.get_for_user(self.request.user)
        return context


class SharedSearchListView(LoginRequiredMixin, AdvancedSearchPermissionMixin, TemplateView):
    template_name = "advanced_search/shared_searches.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["shared_searches"] = SavedSearch.get_shared_with_user(self.request.user)
        return context


class SavedSearchModifyView(LoginRequiredMixin, CanAccessSavedSearchMixin, TemplateView):
    template_name = "advanced_search/save_search_form_body.html"

    def get_context_data(self, **kwargs):
        saved_search = self.saved_search

        context = super().get_context_data(**kwargs)
        context["save_search_form"] = SearchCreateForm(instance=saved_search, user=self.request.user)
        context["show_metadata_note"] = not self.request.GET.get("update")
        return context

    def post(self, request, pk):
        saved_search = self.saved_search

        query_data_raw = request.POST.get("query_data")
        query_data = {}
        if query_data_raw:
            try:
                query_data = json.loads(query_data_raw)
                if not isinstance(query_data, dict):
                    raise ValueError("query_data must be a JSON object")
            except (json.JSONDecodeError, TypeError, ValueError):
                return JsonResponse({"success": False, "message": "Invalid query data format."}, status=400)

        mutable_post = request.POST.copy()
        mutable_post["query_data"] = json.dumps(query_data)
        form = SearchCreateForm(mutable_post, instance=saved_search, user=request.user)

        if form.is_valid():
            saved_search = form.save(commit=False)
            saved_search.query_data = query_data
            saved_search.save()
            form.save_m2m()

            # Update session to reflect the modified search (only if it's currently loaded)
            session = request.session
            if session.get("loaded_search_id") == saved_search.pk:
                session["loaded_search_name"] = saved_search.name
                session["is_loaded_search_owner"] = saved_search.owner == request.user
                session.save()

            return JsonResponse(
                {
                    "success": True,
                    "message": "Updated successfully.",
                    "search_name": saved_search.name,
                }
            )
        else:
            return JsonResponse(
                {"success": False, "message": "There was an issue updating your search.", "errors": form.errors},
                status=400,
            )


class SavedSearchDetailView(LoginRequiredMixin, AdvancedSearchPermissionMixin, TemplateView):
    template_name = "advanced_search/saved_search_details_modal_content.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        saved_search = get_object_or_404(SavedSearch, pk=kwargs.get("pk"))

        query_data = saved_search.query_data or {}

        structured_fields = []
        search_type = "project"
        if query_data:
            search_type = list(query_data.keys())[0].split("_")[0].title()
            for section_values in query_data.values():
                if isinstance(section_values, dict):
                    for field_name, field_value in section_values.items():
                        if not field_value or (isinstance(field_value, list) and len(field_value) == 0):
                            continue
                        # Strip the search type prefix from field name
                        clean_name = field_name.split("-")[-1]
                        if isinstance(field_value, list):
                            field_value = ", ".join(str(v) for v in field_value)
                        structured_fields.append({"name": clean_name, "value": str(field_value)})

        context.update(
            {
                "name": saved_search.name,
                "description": saved_search.description,
                "created": saved_search.created,
                "modified": saved_search.modified,
                "raw_query_data": saved_search.query_data,
                "owner": saved_search.owner.username,
                "shared_with_users": list(saved_search.shared_with_users.all().values_list("username", flat=True)),
                "shared_with_groups": list(saved_search.shared_with_groups.all().values_list("name", flat=True)),
                "search_type": search_type,
                "structured_fields": structured_fields,
            }
        )
        return context


class SavedSearchCopyView(LoginRequiredMixin, CanAccessSavedSearchMixin, View):
    """View to copy a saved search, creating a new one owned by the current user."""

    def post(self, request, pk):
        original = self.saved_search
        copy = SavedSearch(
            name=f"{original.name} (copy)",
            description=original.description,
            query_data=original.query_data,
            owner=request.user,
        )
        copy.save()

        return JsonResponse(
            {
                "success": True,
                "search_id": copy.pk,
                "apply_url": reverse("apply-saved-search", kwargs={"pk": copy.pk}),
                "message": f"Search copied as '{copy.name}'.",
            }
        )


class SavedSearchDeleteView(LoginRequiredMixin, CanAccessSavedSearchMixin, View):
    def post(self, request, pk):
        saved_search = self.saved_search
        saved_search.delete()
        return JsonResponse({"success": True, "message": "Deleted successfully."})


class AdvancedExportView(LoginRequiredMixin, AdvancedSearchPermissionMixin, View):
    def post(self, request):
        try:
            data = json.loads(request.POST.get("data"))
        except (json.JSONDecodeError, TypeError):
            messages.error(request, "Invalid export data.")
            return HttpResponseRedirect(reverse("advanced-search"))
        columns = data.get("columns")
        if not columns or not isinstance(columns, list):
            messages.error(request, "Nothing to export.")
            return HttpResponseRedirect(reverse("advanced-search"))
        column_names = [column.get("display_name") for column in columns]
        if not column_names:
            messages.error(request, "Nothing to export.")
            return HttpResponseRedirect(reverse("advanced-search"))
        rows = data.get("rows")
        if not rows or not isinstance(rows, dict):
            messages.error(request, "Invalid export data: no rows found.")
            return HttpResponseRedirect(reverse("advanced-search"))
        rows = [value for value in rows.values()]

        rows.insert(0, column_names)
        pseudo_buffer = Echo()
        writer = csv.writer(pseudo_buffer)
        response = StreamingHttpResponse((writer.writerow(row) for row in rows), content_type="text/csv")
        file_name = "data"
        response["Content-Disposition"] = f'attachment; filename="{file_name}.csv"'

        logger.info(f"Admin {request.user.username} exported the advanced search list")

        return response


class ApplySavedSearchView(LoginRequiredMixin, CanAccessSavedSearchMixin, RedirectView):
    pattern_name = "advanced-search"

    def get_redirect_url(self, *args, **kwargs):
        saved_search = self.saved_search

        query_data = saved_search.query_data or {}

        search_type = "project"
        type_map = {"project_search": "project", "allocation_search": "allocation", "user_search": "user"}
        for key in query_data:
            if key in type_map:
                search_type = type_map[key]
                break

        session = self.request.session

        flattened = {}
        for search_type_key, fields in query_data.items():
            if isinstance(fields, dict):
                flattened.update(fields)
            else:
                flattened[search_type_key] = fields

        for prefix in ["projectattribute", "allocationattribute"]:
            add_formset_management_fields(flattened, prefix)

        session["filter_data"] = flattened
        session["search_type"] = search_type
        session["loaded_search_id"] = saved_search.pk
        session["loaded_search_name"] = saved_search.name
        session["loaded_search_owner_id"] = saved_search.owner_id
        session["is_loaded_search_owner"] = saved_search.owner == self.request.user
        session.save()
        return reverse("advanced-search")


class LoadSavedSearchView(LoginRequiredMixin, CanAccessSavedSearchMixin, View):
    """AJAX view to load saved search data and return as JSON for dynamic form population."""

    def get(self, request, *args, **kwargs):
        saved_search = self.saved_search
        query_data = saved_search.query_data
        return JsonResponse(
            {
                "query_data": query_data,
                "search_id": saved_search.pk,
                "search_name": saved_search.name,
                "owner_username": saved_search.owner.username,
                "is_owner": saved_search.owner == request.user,
            }
        )
