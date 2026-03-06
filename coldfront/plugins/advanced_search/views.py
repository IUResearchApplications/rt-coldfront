import csv
import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.utils import cached_property
from django.forms import formset_factory
from django.http import HttpResponseRedirect
from django.http.response import StreamingHttpResponse
from django.urls import reverse
from django.views.generic import TemplateView, View

from coldfront.core.allocation.models import AllocationAttributeType
from coldfront.core.project.models import ProjectAttributeType
from coldfront.core.utils.common import Echo
from coldfront.plugins.advanced_search.forms import (
    AllocationAttributeFormSetHelper,
    AllocationAttributeSearchForm,
    AllocationSearchForm,
    ProjectAttributeFormSetHelper,
    ProjectAttributeSearchForm,
    ProjectSearchForm,
    UserSearchForm,
)
from coldfront.plugins.advanced_search.utils import AllocationTable, ProjectTable, UserTable

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
        return formset(self.request.GET if self.request.GET else None, prefix=prefix, **kwargs)

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
        project_search_form = ProjectSearchForm(self.request.GET, prefix="project_search")
        context["project_form"] = project_search_form
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
        allocation_search_form = AllocationSearchForm(self.request.GET, prefix="allocation_search")
        context["allocation_form"] = allocation_search_form
        selected_resources = None
        if allocation_search_form.is_valid():
            selected_resources = allocation_search_form.cleaned_data.get("resources__name")

        allocation_search_formset = self.create_formset(
            AllocationAttributeSearchForm,
            "allocationattribute",
            form_kwargs={"resources": selected_resources},
        )
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
        user_search_form = UserSearchForm(self.request.GET, prefix="user_search")
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["project_form"] = ProjectSearchForm(prefix="project_search")
        context["allocation_form"] = AllocationSearchForm(prefix="allocation_search")
        context["user_form"] = UserSearchForm(prefix="user_search")
        allocation_search_formset = formset_factory(AllocationAttributeSearchForm, extra=1)
        context["allocationattribute_form"] = allocation_search_formset(prefix="allocationattribute")
        project_search_formset = formset_factory(ProjectAttributeSearchForm, extra=1)
        context["projectattribute_form"] = project_search_formset(prefix="projectattribute")
        context["rows"], context["columns"] = [], []

        submit = self.request.GET.get("submit")
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
                "allocationattribute_helper": AllocationAttributeFormSetHelper(),
                "projectattribute_helper": ProjectAttributeFormSetHelper(),
                "CENTER_BASE_URL": settings.CENTER_BASE_URL,
                "active_tab": context.get("active_tab", "project-search"),
            }
        )
        return context


class AdvancedExportView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        user = self.request.user
        if user.is_superuser:
            return True

        if user.has_perms(["project.can_view_all_projects", "allocation.can_view_all_allocations"]):
            return True

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
