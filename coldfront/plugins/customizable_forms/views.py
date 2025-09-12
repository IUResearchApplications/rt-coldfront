import importlib
import logging
import urllib

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import TemplateView, View
from django.shortcuts import get_object_or_404
from django.http import HttpResponseRedirect
from django.urls import reverse

from coldfront.core.allocation.views import AllocationCreateView
from coldfront.core.project.models import Project
from coldfront.core.project.models import ProjectPermission
from coldfront.core.resource.models import Resource
from coldfront.core.allocation.utils import get_user_resources
from coldfront.core.utils.common import import_from_settings
from coldfront.plugins.customizable_forms.utils import standardize_resource_name


CUSTOMIZABLE_FORMS_ALLOCATION_VIEWS = import_from_settings(
    'CUSTOMIZABLE_FORMS_ALLOCATION_VIEWS', []
)
CUSTOMIZABLE_FORMS_ADDITIONAL_PERSISTANCE_FUNCTIONS = import_from_settings(
    'CUSTOMIZABLE_FORMS_ADDITIONAL_PERSISTANCE_FUNCTIONS', {}
)

logger = logging.getLogger(__name__)


class AllocationResourceSelectionView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'customizable_forms/resource_selection.html'

    def test_func(self):
        """ UserPassesTestMixin Tests"""
        project_obj = get_object_or_404(Project, pk=self.kwargs.get('project_pk'))
        if project_obj.has_perm(self.request.user, ProjectPermission.UPDATE):
            return True

        messages.error(self.request, 'You do not have permission to create a new allocation.')
        return False

    def dispatch(self, request, *args, **kwargs):
        project_obj = get_object_or_404(Project, pk=self.kwargs.get('project_pk'))

        if project_obj.needs_review:
            messages.error(
                request, 'You cannot request a new allocation because you have to review your project first.'
            )
            return HttpResponseRedirect(reverse('project-detail', kwargs={'pk': project_obj.pk}))

        if project_obj.status.name in ['Archived', 'Denied', 'Review Pending', 'Expired', 'Renewal Denied', ]:
            messages.error(
                request,
                'You cannot request a new allocation for a project with status "{}".'.format(project_obj.status.name)
            )
            return HttpResponseRedirect(reverse('project-detail', kwargs={'pk': project_obj.pk}))

        return super().dispatch(request, *args, **kwargs)
    
    def get_resource_categories(cls, resource_objs):
        resource_categories = {}
        for resource_obj in resource_objs:
            resource_type_name = resource_obj.resource_type.name
            if not resource_categories.get(resource_type_name):
                resource_categories[resource_type_name] = {'allocated': set(), 'resources': []}

        return resource_categories
    
    def get_project_resource_count(cls, project_obj):
        project_allocations = project_obj.allocation_set.filter(
            status__name__in=[
                "Active",
                "New",
                "Renewal Requested",
                "Billing Information Submitted",
                "Paid",
                "Payment Pending",
                "Payment Requested",
            ]
        )
        project_resource_count = {}
        for project_allocation in project_allocations:
            resource_name = project_allocation.get_parent_resource.name
            current_count = project_resource_count.get(resource_name, 0)
            project_resource_count[resource_name] = current_count + 1

        return project_resource_count

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project_obj = get_object_or_404(Project, pk=self.kwargs.get('project_pk'))
        project_resource_count = self.get_project_resource_count(project_obj)

        persistant_values = {}
        for variable, func in CUSTOMIZABLE_FORMS_ADDITIONAL_PERSISTANCE_FUNCTIONS.items():
            func_module, func = func.rsplit('.', 1)
            func = getattr(importlib.import_module(func_module), func)
            persistant_values[variable] = func(self.request, project_obj)
        persistant_values['user'] = self.request.user
        persistant_values['project'] = project_obj
        persistant_values['project_resource_count'] = project_resource_count

        resource_objs = get_user_resources(self.request.user).prefetch_related(
            'resource_type', 'resourceattribute_set').order_by('resource_type')
        resource_categories = self.get_resource_categories(resource_objs)
        for resource_obj in resource_objs:
            resource_type_name = resource_obj.resource_type.name

            info_url = ""
            rule_result = {'passed': True, 'title': '', 'description': ''}
            custom_form = CUSTOMIZABLE_FORMS_ALLOCATION_VIEWS.get(resource_obj.name)
            if custom_form:
                info_url = custom_form.get('info_url')
                for rule_func in custom_form.get('rule_functions'):
                    rule_func_module, rule_func = rule_func.rsplit('.', 1)
                    rule_func = getattr(importlib.import_module(rule_func_module), rule_func)
                    rule_result = rule_func(resource_obj, persistant_values)
                    if not rule_result.get('passed'):
                        break

            resource_categories[resource_type_name]['resources'].append(
                {
                    'resource': resource_obj,
                    'resource_count': project_resource_count.get(resource_obj.name),
                    'info_url': info_url,
                    'rule_result': rule_result
                }
            )

        after_project_creation = self.request.GET.get('after_project_creation')
        if after_project_creation is None:
            after_project_creation = 'false'
        context['after_project_creation'] = after_project_creation
        context['resource_types'] = resource_categories
        context['project_obj'] = project_obj

        return context


class DispatchView(LoginRequiredMixin, View):
    def dispatch(self, request, project_pk, resource_pk, *args, **kwargs):
        resource_obj = get_object_or_404(Resource, pk=resource_pk)
        return HttpResponseRedirect(
            self.reverse_with_params(
                reverse(
                    'resource-form',
                    kwargs={
                        'project_pk': project_pk,
                        'resource_pk': resource_pk,
                        'resource_name': standardize_resource_name(resource_obj.name)
                    }
                ),
                after_project_creation = self.request.GET.get('after_project_creation')
            )
        )

    def reverse_with_params(self, path, **kwargs):
        return path + '?' + urllib.parse.urlencode(kwargs)


class GenericView(AllocationCreateView):
    pass
