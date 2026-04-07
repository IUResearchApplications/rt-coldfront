import csv
import json
import logging

from crispy_forms.utils import render_crispy_form
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.utils import cached_property
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
from coldfront.plugins.advanced_search.models import SavedSearch
from coldfront.plugins.advanced_search.utils import (
    AllocationTable,
    ProjectTable,
    UserTable,
    get_saved_searches,
    get_shared_searches,
)

logger = logging.getLogger(__name__)


class AdvancedSearchView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "advanced_search/advanced_search.html"

    def test_func(self):
        user = self.request.user
        if user.is_superuser:
            return True

        if user.has_perms(["project.can_view_all_projects", "allocation.can_view_all_allocations"]):
            return True

    @cached_property
    def usage_attribute_ids(self):
        return {
            "allocation": set(AllocationAttributeType.objects.filter(has_usage=True).values_list("id", flat=True)),
            "project": set(ProjectAttributeType.objects.filter(has_usage=True).values_list("id", flat=True)),
        }

    def create_formset(self, form, prefix, **kwargs):
        formset = formset_factory(form, extra=1)
        filter_data = self.request.session.get("filter_data")
        return formset(filter_data if filter_data else None, prefix=prefix, **kwargs)

    def clean_formset_data(self, formset, usage_attribute_ids, attribute_type):
        cleaned = []
        for form in formset:
            if not form.is_valid():
                continue
            data = form.cleaned_data
            attribute_obj = data.get(f"{attribute_type}__name")
            if not attribute_obj or attribute_obj.id not in usage_attribute_ids:
                data[f"{attribute_type}__has_usage"] = "0"
            cleaned.append(data)
        return cleaned

    def handle_project_search(self, context):
        context["active_tab"] = "project-search"
        project_search_form = context["project_form"]
        project_search_formset = self.create_formset(ProjectAttributeSearchForm, "projectattribute")
        project_attribute_data = self.clean_formset_data(
            project_search_formset,
            self.usage_attribute_ids["project"],
            "projectattribute",
        )

        if project_search_form.is_valid():
            table = ProjectTable(project_search_form.cleaned_data, project_attribute_data)
            context["rows"], context["columns"] = table.build_table()
            context["projectattribute_form"] = project_search_formset
        else:
            context["project_form"] = ProjectSearchForm(prefix="project_search")

    def handle_allocation_search(self, context):
        context["active_tab"] = "allocation-search"
        allocation_search_form = context["allocation_form"]
        allocation_search_formset = context["allocationattribute_form"]
        allocation_attribute_data = self.clean_formset_data(
            allocation_search_formset,
            self.usage_attribute_ids["allocation"],
            "allocationattribute",
        )

        if allocation_search_form.is_valid():
            table = AllocationTable(allocation_search_form.cleaned_data, allocation_attribute_data)
            context["rows"], context["columns"] = table.build_table()
            context["allocationattribute_form"] = allocation_search_formset
        else:
            context["allocation_form"] = AllocationSearchForm(prefix="allocation_search")

    def handle_user_search(self, context):
        context["active_tab"] = "user-search"
        user_search_form = context["user_form"]
        if user_search_form.is_valid():
            table = UserTable(user_search_form.cleaned_data)
            context["rows"], context["columns"] = table.build_table()
        else:
            context["user_form"] = UserSearchForm(prefix="user_search")

    def linked_allocation_attribute_types(self):
        queryset = AllocationAttributeType.objects.prefetch_related("linked_resources")
        linked = {}
        for allocation_attribute_type_objs in queryset:
            for resource in allocation_attribute_type_objs.linked_resources.all():
                linked.setdefault(resource.id, []).append(
                    f'<option value="{allocation_attribute_type_objs.id}">{allocation_attribute_type_objs}</option>'
                )
        return linked

    def post(self, request, *args, **kwargs):
        session = request.session
        session["filter_data"] = request.POST.dict()

        return HttpResponseRedirect(f"{reverse('advanced-search')}?submit={request.POST.get('submit')}")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        filter_data = self.request.session.get("filter_data")
        if filter_data:
            project_search_form_data = {k: v for k, v in filter_data.items() if k.startswith("project_search-")}
            allocation_search_form_data = {k: v for k, v in filter_data.items() if k.startswith("allocation_search-")}
            user_search_form_data = {k: v for k, v in filter_data.items() if k.startswith("user_search-")}
            project_search_form = ProjectSearchForm(project_search_form_data, prefix="project_search")
            allocation_search_form = AllocationSearchForm(allocation_search_form_data, prefix="allocation_search")
            user_search_form = UserSearchForm(user_search_form_data, prefix="user_search")
        else:
            project_search_form = ProjectSearchForm(prefix="project_search")
            allocation_search_form = AllocationSearchForm(prefix="allocation_search")
            user_search_form = UserSearchForm(prefix="user_search")

        allocation_search_formset = formset_factory(AllocationAttributeSearchForm, extra=1)
        context["allocationattribute_form"] = allocation_search_formset(prefix="allocationattribute")

        project_search_formset = formset_factory(ProjectAttributeSearchForm, extra=1)
        context["projectattribute_form"] = project_search_formset(prefix="projectattribute")

        project_formset_data = {}
        allocation_formset_data = {}
        formset_data = {}
        if filter_data:
            for key, value in filter_data.items():
                if key.startswith("projectattribute-"):
                    project_formset_data[key] = value
                elif key.startswith("allocationattribute-"):
                    allocation_formset_data[key] = value
                elif not key.startswith("csrfmiddlewaretoken"):
                    formset_data[key] = value

        project_search_formset = formset_factory(ProjectAttributeSearchForm, extra=1)
        context["projectattribute_form"] = project_search_formset(
            project_formset_data if project_formset_data else None, prefix="projectattribute"
        )

        allocation_search_formset = formset_factory(AllocationAttributeSearchForm, extra=1)
        context["allocationattribute_form"] = allocation_search_formset(
            allocation_formset_data if allocation_formset_data else None, prefix="allocationattribute"
        )

        context["rows"], context["columns"] = [], []
        context["project_form"] = project_search_form
        context["allocation_form"] = allocation_search_form
        context["user_form"] = user_search_form
        context["save_search_form"] = SearchCreateForm(user=self.request.user)

        submit = filter_data.get("submit") if filter_data else None
        if submit == "Project Search":
            self.handle_project_search(context)
        elif submit == "Allocation Search":
            self.handle_allocation_search(context)
        elif submit == "User Search":
            self.handle_user_search(context)

        context.update(
            {
                "allocation_attribute_type_ids": list(self.usage_attribute_ids["allocation"]),
                "project_attribute_type_ids": list(self.usage_attribute_ids["project"]),
                "linked_allocation_attribute_types": self.linked_allocation_attribute_types(),
                "allocationattribute_helper": AttributeFormSetHelper("allocation"),
                "projectattribute_helper": AttributeFormSetHelper("project"),
                "CENTER_BASE_URL": settings.CENTER_BASE_URL,
                "active_tab": context.get("active_tab", "project-search"),
            }
        )
        return context


class SavedSearchCreateView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        user = self.request.user
        if user.is_superuser:
            return True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filter_data = self.request.session.get("filter_data", {})
        search_type = self.request.GET.get("search_type", "project")

        initial_query = {}
        if search_type == "project":
            initial_query.update(
                {
                    "project_search": {k: v for k, v in filter_data.items() if k.startswith("project_search-")},
                    "project_attributes": {k: v for k, v in filter_data.items() if k.startswith("projectattribute-")},
                }
            )
        elif search_type == "allocation":
            initial_query.update(
                {
                    "allocation_search": {k: v for k, v in filter_data.items() if k.startswith("allocation_search-")},
                    "allocation_attributes": {
                        k: v for k, v in filter_data.items() if k.startswith("allocationattribute-")
                    },
                }
            )
        elif search_type == "user":
            initial_query.update(
                {
                    "user_search": {k: v for k, v in filter_data.items() if k.startswith("user_search-")},
                }
            )

        context["query_data"] = json.dumps(initial_query)
        context["search_type"] = search_type
        return context

    def post(self, request, *args, **kwargs):
        form = SearchCreateForm(request.POST, user=request.user)
        query_data_raw = request.POST.get("query_data")

        if form.is_valid():
            try:
                query_data = json.loads(query_data_raw) if query_data_raw else {}
                saved_search = form.save(commit=False)
                saved_search.owner = request.user
                saved_search.query_data = query_data
                saved_search.save()

                return JsonResponse({"success": True, "message": "Search saved successfully."})
            except json.JSONDecodeError:
                return JsonResponse({"success": False, "message": "Invalid search data format."}, status=400)
            except Exception as e:
                logger.error(f"Error saving search: {str(e)}")
                return JsonResponse({"success": False, "message": "An unexpected error occurred."}, status=500)
        else:
            return JsonResponse(
                {"success": False, "message": "There was an issue saving your search.", "errors": form.errors},
                status=400,
            )


class SavedSearchListView(LoginRequiredMixin, TemplateView):
    template_name = "advanced_search/saved_searches.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["user_saved_searches"] = get_saved_searches(self.request.user)
        context["shared_searches"] = get_shared_searches(self.request.user)
        return context


class SavedSearchModifyView(LoginRequiredMixin, View):
    def get(self, request, pk):
        saved_search = get_object_or_404(SavedSearch, pk=pk)

        # Check permissions
        if not (
            saved_search.owner == request.user
            or saved_search.shared_with_users.filter(pk=request.user.pk).exists()
            or saved_search.shared_with_groups.filter(groups__in=request.user.groups.all()).exists()
        ):
            return JsonResponse({"error": "Unauthorized"}, status=403)

        form = SearchCreateForm(instance=saved_search, user=request.user)

        return JsonResponse({"form_html": render_crispy_form(form)})

    def post(self, request, pk):
        saved_search = get_object_or_404(SavedSearch, pk=pk)

        if not (
            saved_search.owner == request.user
            or saved_search.shared_with_users.filter(pk=request.user.pk).exists()
            or saved_search.shared_with_groups.filter(groups__in=request.user.groups.all()).exists()
        ):
            return JsonResponse({"error": "Unauthorized"}, status=403)

        form = SearchCreateForm(request.POST, instance=saved_search, user=request.user)

        if form.is_valid():
            form.save()
            return JsonResponse({"success": True, "message": "Search updated successfully."})
        else:
            return JsonResponse(
                {"success": False, "message": "There was an issue updating your search.", "errors": form.errors},
                status=400,
            )


class SavedSearchDetailView(LoginRequiredMixin, View):
    pass


class SavedSearchDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        saved_search = get_object_or_404(SavedSearch, pk=pk)
        saved_search.delete()
        return JsonResponse({"success": True, "message": "Search deleted successfully."})


class AdvancedExportView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        user = self.request.user
        if user.is_superuser:
            return True
        if user.has_perms(["project.can_view_all_projects", "allocation.can_view_all_allocations"]):
            return True
        return False

    def post(self, request):
        data = json.loads(request.POST.get("data"))
        columns = data.get("columns")
        column_names = [column.get("display_name") for column in columns]
        if not column_names:
            messages.error(request, "Nothing to export.")
            return HttpResponseRedirect(reverse("advanced-search"))
        rows = data.get("rows")
        rows = [value for value in rows.values()]

        rows.insert(0, column_names)
        pseudo_buffer = Echo()
        writer = csv.writer(pseudo_buffer)
        response = StreamingHttpResponse((writer.writerow(row) for row in rows), content_type="text/csv")
        file_name = "data"
        response["Content-Disposition"] = f'attachment; filename="{file_name}.csv"'

        logger.info(f"Admin {request.user.username} exported the advanced search list")

        return response


class ApplySavedSearchView(LoginRequiredMixin, RedirectView):
    pattern_name = "advanced-search"

    def get_redirect_url(self, *args, **kwargs):
        saved_search = get_object_or_404(SavedSearch, pk=self.kwargs.get("pk"))

        if not (
            saved_search.owner == self.request.user
            or saved_search.shared_with_users.filter(pk=self.request.user.pk).exists()
            or saved_search.shared_with_groups.filter(groups__in=self.request.user.groups.all()).exists()
        ):
            messages.error(self.request, "You do not have access to this saved search.")
            return reverse("saved-searches")

        query_data = saved_search.query_data
        session = self.request.session
        session["filter_data"] = query_data
        session.save()
        messages.success(self.request, f"Applied saved search: {saved_search.name}")
        return super().get_redirect_url(*args, **kwargs)
