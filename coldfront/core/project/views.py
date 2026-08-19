# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import datetime
import logging
import urllib
from collections import Counter

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.models import User
from django.contrib.messages.views import SuccessMessageMixin
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import transaction
from django.db.models import Q
from django.db.models.query import Prefetch
from django.forms import formset_factory
from django.http import HttpResponse, HttpResponseRedirect
from django.http.response import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import pluralize
from django.urls import reverse
from django.utils.html import format_html
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from django.views.generic.base import TemplateView
from django.views.generic.edit import FormView

from coldfront.core.allocation.models import (
    Allocation,
    AllocationStatusChoice,
    AllocationUser,
    AllocationUserRoleChoice,
)
from coldfront.core.allocation.signals import (
    allocation_expire,
)
from coldfront.core.allocation.utils import parent_resources_prefetch, send_added_user_email
from coldfront.core.grant.models import Grant
from coldfront.core.project.forms import (
    ProjectAddUserForm,
    ProjectAddUsersToAllocationForm,
    ProjectAddUsersToAllocationFormSet,
    ProjectAttributeAddForm,
    ProjectAttributeDeleteForm,
    ProjectAttributeUpdateForm,
    ProjectCreationForm,
    ProjectRemoveUserForm,
    ProjectRequestEmailForm,
    ProjectReviewAllocationForm,
    ProjectReviewEmailForm,
    ProjectReviewForm,
    ProjectSearchForm,
    ProjectUserUpdateForm,
)
from coldfront.core.project.models import (
    Project,
    ProjectAdminComment,
    ProjectAttribute,
    ProjectAttributeType,
    ProjectReview,
    ProjectReviewStatusChoice,
    ProjectStatusChoice,
    ProjectUser,
    ProjectUserMessage,
    ProjectUserRoleChoice,
    ProjectUserStatusChoice,
)
from coldfront.core.project.signals import (
    project_activate,
    project_new,
    project_remove_user,
    project_review_approved,
    project_update,
    project_user_role_changed,
)
from coldfront.core.project.utils import (
    check_if_pis_eligible,
    create_admin_action,
    create_admin_action_for_creation,
    create_admin_action_for_deletion,
    determine_automated_institution_choice,
    generate_project_code,
    get_new_end_date_from_list,
    get_project_user_emails,
    update_project_user_matches,
)
from coldfront.core.publication.models import Publication
from coldfront.core.research_output.models import ResearchOutput
from coldfront.core.user.forms import UserSearchForm
from coldfront.core.user.utils import CombinedUserSearch
from coldfront.core.utils.common import get_domain_url, get_users_accounts, import_from_settings
from coldfront.core.utils.mail import send_email, send_email_template
from coldfront.core.utils.slack import send_message

if "coldfront.plugins.ldap_misc" in settings.INSTALLED_APPS:
    from coldfront.plugins.ldap_misc.utils.project import (
        check_if_pis_eligible,
        update_project_user_matches,
    )
    from coldfront.plugins.ldap_misc.utils.resource import get_users_accounts

EMAIL_ENABLED = import_from_settings("EMAIL_ENABLED", False)
ALLOCATION_ENABLE_ALLOCATION_RENEWAL = import_from_settings("ALLOCATION_ENABLE_ALLOCATION_RENEWAL", True)
ALLOCATION_DEFAULT_ALLOCATION_LENGTH = import_from_settings("ALLOCATION_DEFAULT_ALLOCATION_LENGTH", 365)
PROJECT_DEFAULT_PROJECT_LENGTH = import_from_settings("PROJECT_DEFAULT_PROJECT_LENGTH", 365)
ALLOCATION_DAYS_TO_REVIEW_BEFORE_EXPIRING = import_from_settings("ALLOCATION_DAYS_TO_REVIEW_BEFORE_EXPIRING", 30)
ALLOCATION_DAYS_TO_REVIEW_AFTER_EXPIRING = import_from_settings("ALLOCATION_DAYS_TO_REVIEW_AFTER_EXPIRING", 60)
PROJECT_DAYS_TO_REVIEW_AFTER_EXPIRING = import_from_settings("PROJECT_DAYS_TO_REVIEW_AFTER_EXPIRING", 60)
PROJECT_END_DATE_CARRYOVER_DAYS = import_from_settings("PROJECT_END_DATE_CARRYOVER_DAYS", 90)
PROJECT_DAYS_TO_REVIEW_BEFORE_EXPIRING = import_from_settings("PROJECT_DAYS_TO_REVIEW_BEFORE_EXPIRING", 30)
PROJECT_CODE = import_from_settings("PROJECT_CODE", False)
PROJECT_CODE_PADDING = import_from_settings("PROJECT_CODE_PADDING", False)
SLACK_MESSAGING_ENABLED = import_from_settings("SLACK_MESSAGING_ENABLED", False)
ENABLE_SLATE_PROJECT_SEARCH = import_from_settings("ENABLE_SLATE_PROJECT_SEARCH", False)

if EMAIL_ENABLED:
    EMAIL_DIRECTOR_EMAIL_ADDRESS = import_from_settings("EMAIL_DIRECTOR_EMAIL_ADDRESS")
    EMAIL_SENDER = import_from_settings("EMAIL_SENDER")
    EMAIL_SIGNATURE = import_from_settings("EMAIL_SIGNATURE")
    EMAIL_TICKET_SYSTEM_ADDRESS = import_from_settings("EMAIL_TICKET_SYSTEM_ADDRESS")
    EMAIL_CENTER_NAME = import_from_settings("CENTER_NAME")
    EMAIL_OPT_OUT_INSTRUCTION_URL = import_from_settings("EMAIL_OPT_OUT_INSTRUCTION_URL")
    EMAIL_ALERTS_EMAIL_ADDRESS = import_from_settings("EMAIL_ALERTS_EMAIL_ADDRESS")

PROJECT_UPDATE_FIELDS = import_from_settings(
    "PROJECT_UPDATE_FIELDS",
    [
        "title",
        "description",
        "field_of_science",
    ],
)

logger = logging.getLogger(__name__)
PROJECT_INSTITUTION_EMAIL_MAP = import_from_settings("PROJECT_INSTITUTION_EMAIL_MAP", False)

ADDITIONAL_USER_SEARCH_CLASSES = import_from_settings("ADDITIONAL_USER_SEARCH_CLASSES", [])


class ProjectDetailView(LoginRequiredMixin, UserPassesTestMixin, DetailView):
    model = Project
    template_name = "project/project_detail.html"
    context_object_name = "project"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True

        if self.request.user.has_perm("project.can_view_all_projects"):
            return True

        project_obj = self.get_object()

        if project_obj.projectuser_set.filter(user=self.request.user, status__name="Active").exists():
            return True

        messages.error(self.request, "You do not have permission to view the previous page.")
        return False

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        is_manager = False
        # Can the user update the project?
        project_obj = self.get_object(Project.objects.select_related("status"))
        project_user = project_obj.projectuser_set.select_related("role").filter(user=self.request.user)
        if self.request.user.is_superuser:
            context["is_allowed_to_update_project"] = True
        elif self.request.user.has_perm("project.change_project"):
            context["is_allowed_to_update_project"] = True
        elif project_user:
            project_user = project_user.first()
            if project_user.role.name == "Manager":
                is_manager = True
                context["is_allowed_to_update_project"] = True
            else:
                context["is_allowed_to_update_project"] = False
        else:
            context["is_allowed_to_update_project"] = False

        attributes_query = project_obj.projectattribute_set.select_related("proj_attr_type", "projectattributeusage")
        if self.request.user.is_superuser or self.request.user.has_perm("project.view_projectattribute"):
            attributes_with_usage = [
                attribute
                for attribute in attributes_query.all().order_by("proj_attr_type__name")
                if hasattr(attribute, "projectattributeusage")
            ]

            attributes = [attribute for attribute in attributes_query.all().order_by("proj_attr_type__name")]

        else:
            attributes_with_usage = [
                attribute
                for attribute in attributes_query.filter(proj_attr_type__is_private=False)
                if hasattr(attribute, "projectattributeusage")
            ]

            attributes = [attribute for attribute in attributes_query.filter(proj_attr_type__is_private=False)]

        invalid_attributes = []
        for attribute in attributes_with_usage:
            try:
                float(attribute.value)
                float(attribute.projectattributeusage.value)
            except ValueError:
                logger.error("Project attribute '%s' is not an int but has a usage", attribute.proj_attr_type.name)
                invalid_attributes.append(attribute)

        for a in invalid_attributes:
            attributes_with_usage.remove(a)

        # Only show 'Active Users'
        project_users = (
            project_obj.projectuser_set.select_related("user", "role", "status")
            .filter(status__name="Active")
            .order_by("user__username")
        )

        context["mailto"] = "mailto:" + ",".join([user.user.email for user in project_users])

        allocations = Allocation.objects.select_related("status").prefetch_related(
            parent_resources_prefetch(),
            Prefetch(
                "allocationuser_set",
                queryset=AllocationUser.objects.select_related("status").filter(user=self.request.user),
                to_attr="request_user",
            ),
        )
        if (
            self.request.user.is_superuser
            or is_manager
            or self.request.user.has_perm("allocation.can_view_all_allocations")
        ):
            allocations = allocations.filter(project=project_obj).order_by("-end_date")
        else:
            allocations = (
                Allocation.objects.filter(
                    Q(project=project_obj)
                    & Q(project__projectuser__user=self.request.user)
                    & Q(
                        project__projectuser__status__name__in=[
                            "Active",
                        ]
                    )
                    & Q(allocationuser__user=self.request.user)
                    & Q(
                        allocationuser__status__name__in=[
                            "Active",
                            "Invited",
                            "Pending",
                            "Disabled",
                            "Retired",
                            "PendingEULA",
                        ]
                    )
                )
                .distinct()
                .order_by("-end_date")
            )

        user_status = []
        for allocation in allocations:
            allocation_user = allocation.request_user
            if allocation_user:
                user_status.append(allocation_user[0].status.name)

        note_set = project_obj.projectusermessage_set
        if self.request.user.is_superuser or self.request.user.has_perm("project.view_projectusermessage"):
            notes = note_set.all()
        else:
            notes = note_set.filter(is_private=False)

        if self.request.user.is_superuser:
            context["admin_notes"] = project_obj.projectadmincomment_set.order_by("-modified")

        context["notes"] = notes
        context["project_messages"] = notes.order_by("-created")
        context["publications"] = (
            Publication.objects.select_related("source").filter(project=project_obj, status="Active").order_by("-year")
        )
        context["research_outputs"] = ResearchOutput.objects.filter(project=project_obj).order_by("-created")
        context["grants"] = Grant.objects.select_related("status").filter(
            project=project_obj, status__name__in=["Active", "Pending", "Archived"]
        )
        context["allocations"] = allocations
        context["user_allocation_status"] = user_status
        context["attributes"] = attributes
        context["attributes_with_usage"] = attributes_with_usage
        context["project_users"] = project_users
        context["ALLOCATION_ENABLE_ALLOCATION_RENEWAL"] = ALLOCATION_ENABLE_ALLOCATION_RENEWAL
        context["PROJECT_DAYS_TO_REVIEW_AFTER_EXPIRING"] = PROJECT_DAYS_TO_REVIEW_AFTER_EXPIRING
        context["ALLOCATION_DAYS_TO_REVIEW_BEFORE_EXPIRING"] = ALLOCATION_DAYS_TO_REVIEW_BEFORE_EXPIRING
        context["ALLOCATION_DAYS_TO_REVIEW_AFTER_EXPIRING"] = ALLOCATION_DAYS_TO_REVIEW_AFTER_EXPIRING
        context["enable_customizable_forms"] = "coldfront.plugins.customizable_forms" in settings.INSTALLED_APPS
        context["display_pi_change_request"] = "coldfront.plugins.pi_change_request" in settings.INSTALLED_APPS

        try:
            context["ondemand_url"] = settings.ONDEMAND_URL
        except AttributeError:
            pass

        context["expand_accordion"] = "show" if context["is_allowed_to_update_project"] else ""

        return context


class ProjectListView(LoginRequiredMixin, ListView):
    model = Project
    template_name = "project/project_list.html"
    prefetch_related = ["pi", "status", "field_of_science"]
    context_object_name = "project_list"
    paginate_by = 25

    def get_queryset(self):
        order_by = self.request.GET.get("order_by", "id")
        direction = self.request.GET.get("direction", "asc")
        if order_by != "name":
            if direction == "asc":
                direction = ""
            if direction == "des":
                direction = "-"
            order_by = direction + order_by

        project_search_form = ProjectSearchForm(self.request.GET)

        projects = Project.objects.prefetch_related("pi", "field_of_science", "status")

        if project_search_form.is_valid():
            data = project_search_form.cleaned_data
            if data.get("show_all_projects") and (
                self.request.user.is_superuser or self.request.user.has_perm("project.can_view_all_projects")
            ):
                projects = (
                    Project.objects.select_related("pi", "field_of_science", "status", "type")
                    .filter(
                        status__name__in=[
                            "New",
                            "Active",
                            "Waiting For Admin Approval",
                            "Contacted By Admin",
                            "Review Pending",
                            "Expired",
                        ]
                    )
                    .order_by(order_by)
                )
            else:
                projects = (
                    Project.objects.select_related("pi", "field_of_science", "status", "type")
                    .filter(
                        Q(
                            status__name__in=[
                                "New",
                                "Active",
                                "Waiting For Admin Approval",
                                "Contacted By Admin",
                                "Review Pending",
                                "Expired",
                            ]
                        )
                        & Q(projectuser__user=self.request.user)
                        & Q(projectuser__status__name="Active")
                    )
                    .order_by(order_by)
                )

            # Last Name
            if data.get("title"):
                projects = projects.filter(title__icontains=data.get("title"))

            # Last Name
            if data.get("last_name"):
                projects = projects.filter(pi__last_name__icontains=data.get("last_name"))

            # Username
            if data.get("username"):
                projects = projects.filter(
                    Q(pi__username__icontains=data.get("username"))
                    | Q(projectuser__user__username__icontains=data.get("username"))
                    & Q(projectuser__status__name="Active")
                )

            # Field of Science
            if data.get("field_of_science"):
                projects = projects.filter(field_of_science__description__icontains=data.get("field_of_science"))

        else:
            projects = (
                Project.objects.select_related("pi", "field_of_science", "status", "type")
                .filter(
                    Q(
                        status__name__in=[
                            "New",
                            "Active",
                            "Waiting For Admin Approval",
                            "Contacted By Admin",
                            "Review Pending",
                            "Expired",
                        ]
                    )
                    & Q(projectuser__user=self.request.user)
                    & Q(projectuser__status__name="Active")
                )
                .order_by(order_by)
            )

        return projects.order_by(order_by).distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        projects_count = self.get_queryset().count()
        context["projects_count"] = projects_count

        context["enabled_pi_search"] = "coldfront.plugins.pi_search" in settings.INSTALLED_APPS
        context["enabled_slate_project_search"] = ENABLE_SLATE_PROJECT_SEARCH

        project_search_form = ProjectSearchForm(self.request.GET)
        if project_search_form.is_valid():
            context["project_search_form"] = project_search_form
            data = project_search_form.cleaned_data
            filter_parameters = ""
            for key, value in data.items():
                if value:
                    if isinstance(value, list):
                        for ele in value:
                            filter_parameters += "{}={}&".format(key, ele)
                    else:
                        filter_parameters += "{}={}&".format(key, value)
            context["project_search_form"] = project_search_form
        else:
            filter_parameters = None
            context["project_search_form"] = ProjectSearchForm()

        order_by = self.request.GET.get("order_by")
        if order_by:
            direction = self.request.GET.get("direction")
            filter_parameters_with_order_by = filter_parameters + "order_by=%s&direction=%s&" % (order_by, direction)
        else:
            filter_parameters_with_order_by = filter_parameters

        if filter_parameters:
            context["expand_accordion"] = "show"

        context["filter_parameters"] = filter_parameters
        context["filter_parameters_with_order_by"] = filter_parameters_with_order_by
        context["PROJECT_INSTITUTION_EMAIL_MAP"] = PROJECT_INSTITUTION_EMAIL_MAP
        context["PROJECT_DAYS_TO_REVIEW_AFTER_EXPIRING"] = PROJECT_DAYS_TO_REVIEW_AFTER_EXPIRING

        project_list = context.get("project_list")
        paginator = Paginator(project_list, self.paginate_by)

        page = self.request.GET.get("page")

        try:
            project_list = paginator.page(page)
        except PageNotAnInteger:
            project_list = paginator.page(1)
        except EmptyPage:
            project_list = paginator.page(paginator.num_pages)

        return context


class ProjectArchivedListView(LoginRequiredMixin, ListView):
    model = Project
    template_name = "project/project_archived_list.html"
    prefetch_related = [
        "pi",
        "status",
        "field_of_science",
    ]
    context_object_name = "project_list"
    paginate_by = 10

    def get_queryset(self):
        order_by = self.request.GET.get("order_by", "id")
        direction = self.request.GET.get("direction", "")
        if order_by != "name":
            if direction == "des":
                direction = "-"
            order_by = direction + order_by

        project_search_form = ProjectSearchForm(self.request.GET)

        if project_search_form.is_valid():
            data = project_search_form.cleaned_data
            if data.get("show_all_projects") and (
                self.request.user.is_superuser or self.request.user.has_perm("project.can_view_all_projects")
            ):
                projects = (
                    Project.objects.prefetch_related(
                        "pi",
                        "field_of_science",
                        "status",
                    )
                    .filter(
                        status__name__in=[
                            "Archived",
                        ]
                    )
                    .order_by(order_by)
                )
            else:
                projects = (
                    Project.objects.prefetch_related(
                        "pi",
                        "field_of_science",
                        "status",
                    )
                    .filter(
                        Q(
                            status__name__in=[
                                "Archived",
                            ]
                        )
                        & Q(projectuser__user=self.request.user)
                        & Q(projectuser__status__name="Active")
                    )
                    .order_by(order_by)
                )

            # Last Name
            if data.get("last_name"):
                projects = projects.filter(pi__last_name__icontains=data.get("last_name"))

            # Username
            if data.get("username"):
                projects = projects.filter(pi__username__icontains=data.get("username"))

            # Field of Science
            if data.get("field_of_science"):
                projects = projects.filter(field_of_science__description__icontains=data.get("field_of_science"))

        else:
            projects = (
                Project.objects.prefetch_related(
                    "pi",
                    "field_of_science",
                    "status",
                )
                .filter(
                    Q(
                        status__name__in=[
                            "Archived",
                        ]
                    )
                    & Q(projectuser__user=self.request.user)
                    & Q(projectuser__status__name="Active")
                )
                .order_by(order_by)
            )

        return projects

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        projects_count = self.get_queryset().count()
        context["projects_count"] = projects_count
        context["expand"] = False

        project_search_form = ProjectSearchForm(self.request.GET)
        if project_search_form.is_valid():
            context["project_search_form"] = project_search_form
            data = project_search_form.cleaned_data
            filter_parameters = ""
            for key, value in data.items():
                if value:
                    if isinstance(value, list):
                        for ele in value:
                            filter_parameters += "{}={}&".format(key, ele)
                    else:
                        filter_parameters += "{}={}&".format(key, value)
            context["project_search_form"] = project_search_form
        else:
            filter_parameters = None
            context["project_search_form"] = ProjectSearchForm()

        order_by = self.request.GET.get("order_by")
        if order_by:
            direction = self.request.GET.get("direction")
            filter_parameters_with_order_by = filter_parameters + "order_by=%s&direction=%s&" % (order_by, direction)
        else:
            filter_parameters_with_order_by = filter_parameters

        if filter_parameters:
            context["expand_accordion"] = "show"

        context["filter_parameters"] = filter_parameters
        context["filter_parameters_with_order_by"] = filter_parameters_with_order_by

        project_list = context.get("project_list")
        paginator = Paginator(project_list, self.paginate_by)

        page = self.request.GET.get("page")

        try:
            project_list = paginator.page(page)
        except PageNotAnInteger:
            project_list = paginator.page(1)
        except EmptyPage:
            project_list = paginator.page(paginator.num_pages)

        return context


class ProjectArchiveProjectView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "project/project_archive.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True

        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))

        if project_obj.pi == self.request.user:
            return True

        if project_obj.projectuser_set.filter(
            user=self.request.user, role__name="Manager", status__name="Active"
        ).exists():
            return True

    def dispatch(self, request, *args, **kwargs):
        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))
        if project_obj.status.name in [
            "Denied",
            "Waiting For Admin Approval",
            "Review Pending",
            "Contacted By Admin",
            "Renewal Denied",
        ]:
            messages.error(request, 'You cannot archive a project with status "{}".'.format(project_obj.status.name))
            return redirect(project_obj)

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        project = get_object_or_404(Project, pk=pk)

        context["project"] = project

        return context

    def post(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        project = get_object_or_404(Project, pk=pk)
        project.archive()
        return redirect(project)


class ProjectCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Project
    template_name_suffix = "_create_form"
    form_class = ProjectCreationForm

    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True

        if self.request.user.userprofile.is_pi:
            return True

    def get_form(self, form_class=None):
        if form_class is None:
            form_class = self.get_form_class()
        return form_class(self.request.user, **self.get_form_kwargs())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["pi_search_url"] = ""
        if "coldfront.plugins.pi_search" in settings.INSTALLED_APPS:
            context["pi_search_url"] = reverse("pi-search-results")

        return context

    def check_max_project_type_count_reached(self, project_obj, pi_obj):
        limit = project_obj.get_env.get("allowed_per_pi")

        if limit is not None:
            pi_projects_count = pi_obj.project_set.filter(
                type=project_obj.type,
                status__name__in=["Active", "Waiting For Admin Approval", "Contacted By Admin", "Review Pending"],
            ).count()
            limit = pi_obj.userprofile.limit_overrides.get("project", {}).get(project_obj.type.name.lower(), limit)
            return pi_projects_count >= int(limit)

        return False

    def form_valid(self, form):
        project_obj = form.save(commit=False)
        form.instance.pi = form.cleaned_data.get("pi")
        pi_is_requestor = form.instance.pi == form.instance.requestor
        form.instance.status = ProjectStatusChoice.objects.get(name="Waiting For Admin Approval")

        if self.check_max_project_type_count_reached(form.instance, form.instance.pi):
            if pi_is_requestor:
                messages.error(self.request, "You have reached the max projects you can have of this type.")
            else:
                messages.error(self.request, "Your PI has reached the max projects they can have of this type.")
            return super().form_invalid(form)

        env = project_obj.get_env or {}

        end_date = get_new_end_date_from_list(env.get("expiry_dates"), buffer_days=PROJECT_END_DATE_CARRYOVER_DAYS)
        if end_date is None:
            logger.error(f"End date for new project request was set to None on date {datetime.date.today()}")
            messages.error(
                self.request, "Something went wrong while submitting this project request. Please try again later."
            )
            return super().form_invalid(form)

        project_obj.end_date = end_date

        addtl_fields = env.get("addtl_fields", [])
        for field in addtl_fields:
            if not form.cleaned_data.get(field):
                messages.error(self.request, f"You must provide a {field} for a {project_obj.type} project.")
                return super().form_invalid(form)

        with transaction.atomic():
            project_obj.save()
            self.object = project_obj

            for field in addtl_fields:
                ProjectAttribute.objects.create(
                    project=project_obj,
                    proj_attr_type=ProjectAttributeType.objects.get(name=field.replace("_", " ").title()),
                    value=form.cleaned_data.get(field),
                )

            ProjectUser.objects.create(
                user=self.request.user,
                project=project_obj,
                role=ProjectUserRoleChoice.objects.get(name="Manager"),
                status=ProjectUserStatusChoice.objects.get(name="Active"),
            )
            if not pi_is_requestor:
                ProjectUser.objects.create(
                    user=form.instance.pi,
                    project=project_obj,
                    role=ProjectUserRoleChoice.objects.get(name="Manager"),
                    status=ProjectUserStatusChoice.objects.get(name="Active"),
                )

            if PROJECT_CODE:
                """
                Set the ProjectCode object, if PROJECT_CODE is defined.
                If PROJECT_CODE_PADDING is defined, the set amount of padding will be added to PROJECT_CODE.
                """
                project_type_initial = form.instance.type.name[0]
                project_obj.project_code = generate_project_code(
                    project_type_initial, project_obj.pk, PROJECT_CODE_PADDING or 0
                )
                project_obj.save(update_fields=["project_code"])

            if PROJECT_INSTITUTION_EMAIL_MAP:
                determine_automated_institution_choice(project_obj, PROJECT_INSTITUTION_EMAIL_MAP)

            response = super().form_valid(form)

        domain_url = get_domain_url(self.request)
        project_review_url = reverse("project-review-list")

        if SLACK_MESSAGING_ENABLED:
            url = "{}{}".format(domain_url, project_review_url)
            send_message(
                f'A new request for project "{project_obj.title}" with id {project_obj.pk} has been submitted. You can view it here: {url}'
            )
        if EMAIL_ENABLED:
            template_context = {
                "url": "{}{}".format(domain_url, project_review_url),
                "project_title": project_obj.title,
                "project_id": project_obj.pk,
            }
            send_email_template(
                "New Project Request", "email/new_project_request.txt", template_context, [EMAIL_ALERTS_EMAIL_ADDRESS]
            )

            if not pi_is_requestor:
                project_url = reverse("project-detail", kwargs={"pk": project_obj.pk})
                template_context = {
                    "center_name": EMAIL_CENTER_NAME,
                    "project_title": project_obj.title,
                    "requestor_first_name": form.instance.requestor.first_name,
                    "requestor_last_name": form.instance.requestor.last_name,
                    "requestor_username": form.instance.requestor.username,
                    "project_url": "{}{}".format(domain_url, project_url),
                    "help_email": EMAIL_TICKET_SYSTEM_ADDRESS,
                    "signature": EMAIL_SIGNATURE,
                }

                send_email_template(
                    "PI For Project Request",
                    "email/pi_project_request.txt",
                    template_context,
                    [form.instance.pi.email],
                    EMAIL_TICKET_SYSTEM_ADDRESS,
                )

                logger.info(f"Email sent to pi {form.instance.pi.username} (project pk={project_obj.pk})")

        # project signals
        project_new.send(sender=self.__class__, project_obj=project_obj)

        logger.info(f"User {form.instance.requestor.username} created a new project (project pk={project_obj.pk})")
        return response

    def reverse_with_params(self, path, **kwargs):
        return path + "?" + urllib.parse.urlencode(kwargs)

    def get_success_url(self):
        url_name = "allocation-create"
        if "coldfront.plugins.customizable_forms" in settings.INSTALLED_APPS:
            url_name = "custom-allocation-create"

        return self.reverse_with_params(
            reverse(url_name, kwargs={"project_pk": self.object.pk}), after_project_creation="true"
        )


class ProjectUpdateView(SuccessMessageMixin, LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Project
    template_name_suffix = "_update_form"
    fields = PROJECT_UPDATE_FIELDS
    success_message = "Project updated."

    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True

        project_obj = self.get_object()

        if self.request.user.has_perm("project.change_project"):
            return True

        if project_obj.pi == self.request.user:
            return True

        if project_obj.projectuser_set.filter(
            user=self.request.user, role__name="Manager", status__name="Active"
        ).exists():
            return True

    def dispatch(self, request, *args, **kwargs):
        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))

        if PROJECT_CODE and project_obj.project_code == "":
            """
            Updates project code if no value was set, providing the feature is activated.
            """
            project_obj.project_code = generate_project_code(
                project_obj.type.name[0], project_obj.pk, PROJECT_CODE_PADDING or 0
            )
            project_obj.save(update_fields=["project_code"])

        if project_obj.status.name in ["Archived", "Denied", "Expired", "Renewal Denied"]:
            messages.error(request, f"You cannot update a project with status {project_obj.status.name}.")
            return redirect(project_obj)
        else:
            return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        render = super().post(request, *args, **kwargs)
        project_obj = self.get_object()
        if SLACK_MESSAGING_ENABLED:
            url = f"{get_domain_url(self.request)}{reverse('project-detail', kwargs={'pk': project_obj.pk})}"
            send_message(
                f'Project "{project_obj.title}" with id {project_obj.pk} was updated. You can view it here: {url}'
            )
        logger.info(f"User {self.request.user.username} updated a project (project pk={project_obj.pk})")
        return render

    def get_success_url(self):
        # project signals
        project_update.send(sender=self.__class__, project_obj=self.object)
        return super().get_success_url()


class ProjectAddUsersSearchView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "project/project_add_users.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True

        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))

        if project_obj.pi == self.request.user:
            return True

        if project_obj.projectuser_set.filter(
            user=self.request.user, role__name="Manager", status__name="Active"
        ).exists():
            return True

    def dispatch(self, request, *args, **kwargs):
        project_obj = get_object_or_404(Project.objects.select_related("status"), pk=self.kwargs.get("pk"))
        if project_obj.status.name in ["Archived", "Denied", "Expired", "Renewal Denied"]:
            messages.error(
                request, 'You cannot add users to a project with status "{}".'.format(project_obj.status.name)
            )
            return redirect(project_obj.pk)
        else:
            return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        context["user_search_form"] = UserSearchForm()
        context["project"] = Project.objects.get(pk=self.kwargs.get("pk"))
        after_project_creation = self.request.GET.get("after_project_creation")
        context["after_project_creation"] = str(after_project_creation == "true").lower()
        return context


class ProjectAddUsersSearchResultsView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "project/add_user_search_results.html"
    raise_exception = True

    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True

        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))

        if project_obj.pi == self.request.user:
            return True

        if project_obj.projectuser_set.filter(
            user=self.request.user, role__name="Manager", status__name="Active"
        ).exists():
            return True

    def dispatch(self, request, *args, **kwargs):
        project_obj = get_object_or_404(Project.objects.select_related("status"), pk=self.kwargs.get("pk"))
        if project_obj.status.name in ["Archived", "Denied", "Expired", "Renewal Denied"]:
            messages.error(
                request, 'You cannot add users to a project with status "{}".'.format(project_obj.status.name)
            )
            return redirect(project_obj.pk)
        else:
            return super().dispatch(request, *args, **kwargs)

    def get_initial_data(self, allocation_objs):
        initial_data = []
        for allocation_obj in allocation_objs:
            resource = allocation_obj.get_parent_resource
            initial_data.append(
                {
                    "pk": allocation_obj.pk,
                    "resource": resource.name,
                    "details": allocation_obj.get_information,
                    "resource_type": resource.resource_type.name,
                    "status": allocation_obj.status.name,
                }
            )
        return initial_data

    def get_allocation_user_roles(self, allocations):
        return [allocation.get_user_roles().values_list("name", flat=True) for allocation in allocations]

    def post(self, request, *args, **kwargs):
        user_search_string = request.POST.get("q")
        search_by = request.POST.get("search_by")
        pk = self.kwargs.get("pk")

        project_obj = get_object_or_404(Project, pk=pk)

        users_to_exclude = [
            ele.user.username
            for ele in project_obj.projectuser_set.select_related("user").filter(status__name="Active")
        ]

        cobmined_user_search_obj = CombinedUserSearch(user_search_string, search_by, users_to_exclude)

        context = cobmined_user_search_obj.search()
        after_project_creation = request.POST.get("after_project_creation")
        context["after_project_creation"] = str(after_project_creation == "true").lower()

        matches = context.get("matches")
        context["num_matches"] = len(matches)
        matches = update_project_user_matches(matches)

        if matches:
            formset = formset_factory(ProjectAddUserForm, max_num=len(matches))
            formset = formset(initial=matches, prefix="userform")
            context["formset"] = formset
            context["user_search_string"] = user_search_string
            context["search_by"] = search_by

        if len(user_search_string.split()) > 1:
            users_already_in_project = []
            for ele in user_search_string.split():
                if ele in users_to_exclude:
                    users_already_in_project.append(ele)
            context["users_already_in_project"] = users_already_in_project

        status_list = ["Active", "New", "Renewal Requested", "Billing Information Submitted"]
        allocations = project_obj.allocation_set.filter(status__name__in=status_list, is_locked=False).exclude(
            resources__name="Geode-Project"
        )
        initial_data = self.get_initial_data(allocations)
        allocation_formset = formset_factory(
            ProjectAddUsersToAllocationForm, max_num=len(initial_data), formset=ProjectAddUsersToAllocationFormSet
        )
        roles = self.get_allocation_user_roles(allocations)
        allocation_formset = allocation_formset(
            initial=initial_data, prefix="allocationform", form_kwargs={"roles": roles}
        )

        account_statuses = {}
        for allocation in allocations:
            resource_obj = allocation.get_parent_resource
            if resource_obj.name not in account_statuses:
                account_statuses[resource_obj.name] = resource_obj.get_user_account_statuses(
                    [match.get("username") for match in matches]
                )
        context["account_statuses"] = account_statuses

        # The following block of code is used to hide/show the allocation div in the form.
        if initial_data:
            div_allocation_class = "placeholder_div_class"
        else:
            div_allocation_class = "d-none"
        context["div_allocation_class"] = div_allocation_class
        ###

        context["pk"] = pk
        context["allocation_form"] = allocation_formset
        context["current_num_managers"] = project_obj.get_current_num_managers()
        context["max_managers"] = project_obj.max_managers
        return render(request, self.template_name, context)


class ProjectAddUsersView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True

        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))

        if project_obj.pi == self.request.user:
            return True

        if project_obj.projectuser_set.filter(
            user=self.request.user, role__name="Manager", status__name="Active"
        ).exists():
            return True

    def dispatch(self, request, *args, **kwargs):
        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))
        if project_obj.status.name in ["Archived", "Denied", "Expired", "Renewal Denied"]:
            messages.error(
                request, 'You cannot add users to a project with status "{}".'.format(project_obj.status.name)
            )
            return redirect(project_obj.pk)
        else:
            return super().dispatch(request, *args, **kwargs)

    def get_initial_data(self, allocations_objs):
        initial_data = []
        for allocation_obj in allocations_objs:
            resource = allocation_obj.get_parent_resource
            initial_data.append(
                {
                    "pk": allocation_obj.pk,
                    "resource": resource.name,
                    "resource_type": resource.resource_type.name,
                    "status": allocation_obj.status.name,
                }
            )

        return initial_data

    def get_allocation_user_roles(self, allocations):
        return [allocation.get_user_roles().values_list("name", flat=True) for allocation in allocations]

    def post(self, request, *args, **kwargs):
        user_search_string = request.POST.get("q")
        search_by = request.POST.get("search_by")
        pk = self.kwargs.get("pk")

        project_obj = get_object_or_404(Project, pk=pk)

        users_to_exclude = [ele.user.username for ele in project_obj.projectuser_set.filter(status__name="Active")]

        cobmined_user_search_obj = CombinedUserSearch(user_search_string, search_by, users_to_exclude)

        context = cobmined_user_search_obj.search()

        matches = context.get("matches")
        matches = update_project_user_matches(matches)

        auto_disable_notifications = project_obj.auto_disable_user_notifications()

        formset = formset_factory(ProjectAddUserForm, max_num=len(matches))
        formset = formset(request.POST, initial=matches, prefix="userform")

        status_list = ["Active", "New", "Renewal Requested", "Billing Information Submitted"]
        allocations = project_obj.allocation_set.filter(status__name__in=status_list, is_locked=False)
        initial_data = self.get_initial_data(allocations)

        allocation_formset = formset_factory(
            ProjectAddUsersToAllocationForm, max_num=len(initial_data), formset=ProjectAddUsersToAllocationFormSet
        )
        roles = self.get_allocation_user_roles(allocations)
        allocation_formset = allocation_formset(
            request.POST, initial=initial_data, prefix="allocationform", form_kwargs={"roles": roles}
        )

        project_user_objs = []
        allocations_added_to = {}
        if formset.is_valid() and allocation_formset.is_valid():
            no_accounts = {}
            added_users = {}
            selected_usernames = [
                form.cleaned_data.get("username") for form in formset if form.cleaned_data.get("selected")
            ]
            selected_users_accounts = get_users_accounts(selected_usernames)
            account_statuses_by_resource = {}

            for form in formset:
                user_form_data = form.cleaned_data

                if user_form_data["selected"]:
                    # Will create local copy of user if not already present in local database
                    user_obj, created = User.objects.get_or_create(username=user_form_data.get("username"))
                    if created:
                        user_obj.first_name = user_form_data.get("first_name")
                        user_obj.last_name = user_form_data.get("last_name")
                        user_obj.email = user_form_data.get("email")
                        user_obj.save()

                    role_choice = user_form_data.get("role")

                    # If no more managers can be added then give the user the 'User' role.
                    if role_choice.name == "Manager":
                        if project_obj.check_exceeds_max_managers(1):
                            role_choice = ProjectUserRoleChoice.objects.get(name="User")

                    # Disable notifications for group accounts, or for user accounts
                    # when the project has "Auto Disable User Notifications" set.
                    enable_notifications = not (
                        role_choice.name == "Group" or (role_choice.name == "User" and auto_disable_notifications)
                    )

                    project_user_obj = project_obj.add_user(
                        user_obj, role_choice, signal_sender=self.__class__, enable_notifications=enable_notifications
                    )
                    project_user_objs.append(project_user_obj)

                    username = user_form_data.get("username")
                    no_accounts[username] = set()
                    added_users[username] = []
                    for allocation_form in allocation_formset:
                        cleaned_data = allocation_form.cleaned_data
                        if cleaned_data["selected"]:
                            allocation = allocations.get(pk=cleaned_data["pk"])
                            allocations_added_to.setdefault(allocation, [])

                            resource = allocation.get_parent_resource

                            if resource.pk not in account_statuses_by_resource:
                                account_statuses_by_resource[resource.pk] = resource.get_user_account_statuses(
                                    selected_usernames, selected_users_accounts
                                )
                            account_exists, reason = account_statuses_by_resource[resource.pk].get(username).values()
                            # If the user does not have an account on the resource in the allocation then do not add them to it.
                            if not account_exists:
                                if reason == "no_account":
                                    no_accounts[username].add("IU")
                                elif reason == "no_resource_account":
                                    no_accounts[username].add(resource.name)
                                continue

                            allocation_user_role_obj = AllocationUserRoleChoice.objects.filter(
                                resources=resource, name=cleaned_data["role"]
                            ).first()

                            allocation.add_user(user_obj, signal_sender=self.__class__, role=allocation_user_role_obj)
                            allocations_added_to[allocation].append(project_user_obj)

                            if resource.name not in added_users[username]:
                                added_users[username].append(resource.name)

            self.send_add_users_messages(request, no_accounts, added_users)
            if EMAIL_ENABLED and project_user_objs:
                self.send_add_users_emails(request, project_obj, project_user_objs, allocations_added_to)
            self.log_add_users(request, project_obj, project_user_objs, allocations_added_to)
            messages.success(request, "Added {} users to project.".format(len(project_user_objs)))
        else:
            if not formset.is_valid():
                for error in formset.errors:
                    messages.error(request, error)
            if not allocation_formset.is_valid():
                for error in allocation_formset.errors:
                    messages.error(request, error)
            return redirect(project_obj)

        if request.POST.get("after_project_creation_field") == "true":
            return redirect(
                self.reverse_with_params(
                    reverse("project-detail", kwargs={"pk": project_obj.pk}), after_project_creation="true"
                )
            )

        return redirect(project_obj)

    def send_add_users_messages(self, request, no_accounts, added_users):
        if any(no_accounts.values()):
            warning_message = (
                "The following users were not added to the selected resource allocations due to missing accounts:<ul>"
            )
            for username, no_account_list in no_accounts.items():
                if no_account_list:
                    if "IU" in no_account_list:
                        warning_message += f"<li>{username} is missing an IU account</li>"
                    else:
                        warning_message += f"<li>{username} is missing an account for {', '.join(no_account_list)}</li>"
            warning_message += "</ul>"
            if warning_message != "":
                url = "https://access.iu.edu/Accounts/Create"
                warning_message += f'They cannot be added until they create one. Please direct them to <a href="{url}">{url}</a> to create one.'
                messages.warning(request, format_html(warning_message))

        if any(added_users.values()):
            message = "The following users were added to the selected resource allocations:<ul>"
            for username, resource_list in added_users.items():
                if resource_list:
                    message += (
                        f"<li>{username} was added to these resource allocations: {', '.join(resource_list)}</li>"
                    )
            message += "</ul>"
            messages.success(request, format_html(message))

    def send_add_users_emails(self, request, project_obj, project_user_objs, allocations_added_to):
        domain_url = get_domain_url(self.request)
        project_url = "{}{}".format(domain_url, reverse("project-detail", kwargs={"pk": project_obj.pk}))

        template_context = {
            "center_name": EMAIL_CENTER_NAME,
            "project_title": project_obj.title,
            "project_users": project_user_objs,
            "action_user": f"{request.user.first_name} {request.user.last_name}",
            "url": project_url,
            "signature": EMAIL_SIGNATURE,
        }
        emails = [
            project_user_obj.user.email
            for project_user_obj in project_user_objs
            if project_user_obj.enable_notifications
        ]
        emails.append(project_obj.pi.email)
        send_email_template(
            "Added to Project", "email/project_added_users.txt", template_context, emails, EMAIL_TICKET_SYSTEM_ADDRESS
        )

        if allocations_added_to:
            for allocation, added_project_user_objs in allocations_added_to.items():
                users = [
                    project_user_obj.user
                    for project_user_obj in added_project_user_objs
                    if project_user_obj.enable_notifications
                ]
                emails = set(user.email for user in users)
                if emails:
                    emails.add(project_obj.pi.email)
                    emails.add(request.user.email)
                    send_added_user_email(request, allocation, users, emails)

    def log_add_users(self, request, project_obj, project_user_objs, allocations_added_to):
        if project_user_objs:
            logger.info(
                f"User {request.user.username} added {', '.join(project_user_obj.user.username for project_user_obj in project_user_objs)} "
                f"to a project (project pk={project_obj.pk})"
            )
        if allocations_added_to:
            for allocation, added_project_user_objs in allocations_added_to.items():
                project_users = [project_user_obj.user.username for project_user_obj in added_project_user_objs]
                if project_users:
                    logger.info(
                        f"User {request.user.username} added {', '.join(project_users)} to a "
                        f"{allocation.get_parent_resource.name} allocation (allocation pk={allocation.pk})"
                    )

    def reverse_with_params(self, path, **kwargs):
        return path + "?" + urllib.parse.urlencode(kwargs)


class ProjectRemoveUsersView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "project/project_remove_users.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True

        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))

        if project_obj.pi == self.request.user:
            return True

        if project_obj.projectuser_set.filter(
            user=self.request.user, role__name="Manager", status__name="Active"
        ).exists():
            return True

    def dispatch(self, request, *args, **kwargs):
        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))
        if project_obj.status.name in ["Archived", "Denied", "Renewal Denied"]:
            messages.error(
                request, 'You cannot remove users from a project with status "{}".'.format(project_obj.status.name)
            )
            return redirect(project_obj)
        else:
            return super().dispatch(request, *args, **kwargs)

    def get_users_to_remove(self, project_obj):
        users_to_remove = [
            {
                "username": ele.user.username,
                "first_name": ele.user.first_name,
                "last_name": ele.user.last_name,
                "email": ele.user.email,
                "role": ele.role,
            }
            for ele in project_obj.projectuser_set.filter(status__name="Active").order_by("user__username")
            if ele.user != self.request.user and ele.user != project_obj.pi
        ]

        return users_to_remove

    def get(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        project_obj = get_object_or_404(Project, pk=pk)

        users_to_remove = self.get_users_to_remove(project_obj)
        context = {}

        if users_to_remove:
            formset = formset_factory(ProjectRemoveUserForm, max_num=len(users_to_remove))
            formset = formset(initial=users_to_remove, prefix="userform")
            context["formset"] = formset

        context["project"] = project_obj
        context["display_warning"] = project_obj.allocation_set.filter(resources__name="Slate-Project")
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        project_obj = get_object_or_404(Project, pk=pk)

        users_to_remove = self.get_users_to_remove(project_obj)

        formset = formset_factory(ProjectRemoveUserForm, max_num=len(users_to_remove))
        formset = formset(request.POST, initial=users_to_remove, prefix="userform")

        removed_user_objs = []
        removed_users_breakdown = {}
        if formset.is_valid():
            project_user_removed_status_choice = ProjectUserStatusChoice.objects.get(name="Removed")
            for form in formset:
                user_form_data = form.cleaned_data
                if user_form_data["selected"]:
                    user_obj = User.objects.get(username=user_form_data.get("username"))

                    if project_obj.pi == user_obj:
                        continue

                    # get allocation to remove users from
                    allocations_to_remove_user_from = project_obj.allocation_set.filter(
                        status__name__in=["Active", "New", "Renewal Requested", "Expired"]
                    )
                    for allocation in allocations_to_remove_user_from:
                        for allocation_user_obj in allocation.allocationuser_set.filter(user=user_obj).exclude(
                            status__name="Removed"
                        ):
                            removed_users_breakdown.setdefault(allocation_user_obj.user.username, []).append(
                                (allocation.get_parent_resource.name, allocation.get_identifiers.values())
                            )

                            allocation.remove_user(allocation_user_obj, signal_sender=self.__class__)

                    project_user_obj = project_obj.projectuser_set.get(user=user_obj)
                    project_user_obj.status = project_user_removed_status_choice
                    project_user_obj.save()
                    # project signals
                    project_remove_user.send(sender=self.__class__, project_user_pk=project_user_obj.pk)
                    removed_user_objs.append(project_user_obj)
                    removed_users_breakdown.setdefault(project_user_obj.user.username, [(None, ())])

            if removed_user_objs:
                if EMAIL_ENABLED:
                    self.send_remove_users_emails(request, project_obj, removed_user_objs, removed_users_breakdown)
                self.log_removed_users(request, project_obj, removed_user_objs)

                removed_user_count = len(removed_user_objs)
                messages.success(
                    request,
                    "Removed {} user{} from project.".format(removed_user_count, pluralize(removed_user_count)),
                )
        else:
            for error in formset.errors:
                messages.error(request, error)

        return redirect(project_obj)

    def send_remove_users_emails(self, request, project_obj, removed_user_objs, removed_users_breakdown):
        emails = [
            project_user_obj.user.email
            for project_user_obj in removed_user_objs
            if project_user_obj.enable_notifications
        ]
        emails.append(project_obj.pi.email)

        template_context = {
            "center_name": EMAIL_CENTER_NAME,
            "project_title": project_obj.title,
            "removed_users": removed_user_objs,
            "removed_users_breakdown": removed_users_breakdown,
            "action_user": f"{request.user.first_name} {request.user.last_name}",
            "signature": EMAIL_SIGNATURE,
        }

        send_email_template(
            "Removed From Project",
            "email/project_removed_users.txt",
            template_context,
            emails,
            EMAIL_TICKET_SYSTEM_ADDRESS,
        )

    def log_removed_users(self, request, project_obj, removed_user_objs):
        removed_users = [project_user_obj.user.username for project_user_obj in removed_user_objs]
        logger.info(
            f"User {request.user.username} removed {', '.join(removed_users)} from a "
            f"project (project pk={project_obj.pk})"
        )


class ProjectUserDetail(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "project/project_user_detail.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True

        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))

        if project_obj.pi == self.request.user:
            return True

        if project_obj.projectuser_set.filter(
            user=self.request.user, role__name="Manager", status__name="Active"
        ).exists():
            return True

    def get(self, request, *args, **kwargs):
        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))
        project_user_obj = get_object_or_404(ProjectUser, pk=self.kwargs.get("project_user_pk"))

        project_user_update_form = ProjectUserUpdateForm(
            initial={"role": project_user_obj.role, "enable_notifications": project_user_obj.enable_notifications}
        )

        context = {}
        context["project_obj"] = project_obj
        context["project_user_update_form"] = project_user_update_form
        context["project_user_obj"] = project_user_obj

        return render(request, self.template_name, context)

    def project_user_detail_url(self, project_obj, project_user_pk):
        return reverse("project-user-detail", kwargs={"pk": project_obj.pk, "project_user_pk": project_user_pk})

    def post(self, request, *args, **kwargs):
        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))
        project_user_pk = self.kwargs.get("project_user_pk")

        if project_obj.status.name in ["Archived", "Denied", "Expired", "Renewal Denied"]:
            messages.error(request, "You cannot update a user in a(n) {} project.".format(project_obj.status.name))
            return HttpResponseRedirect(self.project_user_detail_url(project_obj, project_user_pk))

        if project_obj.projectuser_set.filter(id=project_user_pk).exists():
            project_user_obj = project_obj.projectuser_set.get(pk=project_user_pk)

            if project_user_obj.user == project_obj.pi:
                messages.error(request, "PI role and email notification option cannot be changed.")
                return HttpResponseRedirect(self.project_user_detail_url(project_obj, project_user_pk))

            project_user_update_form = ProjectUserUpdateForm(
                request.POST,
                initial={
                    "role": project_user_obj.role.name,
                    "enable_notifications": project_user_obj.enable_notifications,
                },
            )

            if project_user_update_form.is_valid():
                form_data = project_user_update_form.cleaned_data
                form_role = form_data.get("role")
                form_enable_notifications = form_data.get("enable_notifications")

                if (
                    form_role == project_user_obj.role
                    and project_user_obj.enable_notifications == form_enable_notifications
                ):
                    return HttpResponseRedirect(self.project_user_detail_url(project_obj, project_user_obj.pk))

                if form_role.name == "Manager" and project_user_obj.role.name != "Manager":
                    if project_obj.get_current_num_managers() >= project_obj.max_managers:
                        messages.error(
                            request,
                            f"This project is at its maximum Managers limit ({project_obj.max_managers}) and cannot have more.",
                        )
                        return HttpResponseRedirect(self.project_user_detail_url(project_obj, project_user_obj.pk))

                old_role = project_user_obj.role
                project_user_obj.role = form_role
                if form_role.name == "Manager":
                    project_user_obj.enable_notifications = True
                elif old_role.name == "Manager" and form_role.name == "User":
                    project_user_obj.enable_notifications = not project_obj.auto_disable_user_notifications()
                else:
                    project_user_obj.enable_notifications = form_enable_notifications
                    logger.info(
                        f"Admin {request.user.username} set {project_user_obj.user.username}'s "
                        f"notifications to {form_enable_notifications} (project pk={project_obj.pk})"
                    )
                project_user_obj.save()

                if project_user_obj.role != old_role:
                    project_user_role_changed.send(sender=self.__class__, project_user_pk=project_user_obj.pk)
                    logger.info(
                        f"User {request.user.username} changed {project_user_obj.user.username}'s "
                        f"role to {form_data.get('role')} (project pk={project_obj.pk})"
                    )

                messages.success(request, "User details updated.")
                return HttpResponseRedirect(self.project_user_detail_url(project_obj, project_user_obj.pk))
            else:
                messages.error(request, project_user_update_form.errors)
                return HttpResponseRedirect(self.project_user_detail_url(project_obj, project_user_obj.pk))


@login_required
def project_update_email_notification(request):
    if request.method == "POST":
        data = request.POST
        project_user_obj = get_object_or_404(ProjectUser, pk=data.get("user_project_id"))

        project_obj = project_user_obj.project

        allowed = False
        if project_obj.pi == request.user:
            allowed = True

        if project_obj.projectuser_set.filter(user=request.user, role__name="Manager", status__name="Active").exists():
            allowed = True

        if project_user_obj.user == request.user:
            allowed = True

        if request.user.is_superuser:
            allowed = True

        if allowed is False:
            return HttpResponse("not allowed", status=403)
        else:
            checked = data.get("checked")
            if checked == "true":
                project_user_obj.enable_notifications = True
                project_user_obj.save()
                return HttpResponse("checked", status=200)
            elif checked == "false":
                project_user_obj.enable_notifications = False
                project_user_obj.save()
                return HttpResponse("unchecked", status=200)
            else:
                return HttpResponse("no checked", status=400)
    else:
        return HttpResponse("no POST", status=400)


class ProjectReviewView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "project/project_review.html"
    login_url = "/"  # redirect URL if fail test_func

    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True

        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))

        if project_obj.pi == self.request.user:
            return True

        if project_obj.projectuser_set.filter(
            user=self.request.user, role__name="Manager", status__name="Active"
        ).exists():
            return True

        messages.error(self.request, "You do not have permissions to review this project.")

    def dispatch(self, request, *args, **kwargs):
        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))

        if not project_obj.needs_review:
            if project_obj.get_env.get("renewable"):
                messages.error(request, "You do not need to review this project.")
            else:
                messages.error(request, "This project cannot be reviewed.")
            return redirect(project_obj)

        if "Auto-Import Project".lower() in project_obj.title.lower():
            messages.error(
                request,
                'You must update the project title before reviewing your project. You cannot have "Auto-Import Project" in the title.',
            )
            return HttpResponseRedirect(reverse("project-update", kwargs={"pk": project_obj.pk}))

        if (
            "We do not have information about your research. Please provide a detailed description of your work and update your field of science. Thank you!"
            in project_obj.description
        ):
            messages.error(request, "You must update the project description before reviewing your project.")
            return HttpResponseRedirect(reverse("project-update", kwargs={"pk": project_obj.pk}))

        return super().dispatch(request, *args, **kwargs)

    def get_allocation_data(self, project_obj):
        allocations = project_obj.allocation_set.filter(
            status__name__in=["Active", "Expired"], is_locked=False, resources__requires_payment=False
        )
        initial_data = []
        for allocation in allocations:
            if (
                ALLOCATION_DAYS_TO_REVIEW_AFTER_EXPIRING >= 0
                and allocation.expires_in < -ALLOCATION_DAYS_TO_REVIEW_AFTER_EXPIRING
            ):
                continue

            data = {
                "pk": allocation.pk,
                "resource": allocation.get_resources_as_string,
                "users": ", ".join(
                    [
                        "{} {}".format(ele.user.first_name, ele.user.last_name)
                        for ele in allocation.allocationuser_set.filter(
                            status__name__in=["Active", "Invited", "Pending", "Disabled", "Retired"]
                        ).order_by("user__last_name")
                    ]
                ),
                "status": allocation.status,
                "expires_on": allocation.end_date,
                "renew": True,
            }
            initial_data.append(data)

        return initial_data

    def get(self, request, *args, **kwargs):
        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))
        project_review_form = ProjectReviewForm()

        context = {}
        context["project"] = project_obj
        context["project_review_form"] = project_review_form
        context["project_users"] = ", ".join(
            [
                "{} {}".format(ele.user.first_name, ele.user.last_name)
                for ele in project_obj.projectuser_set.filter(status__name="Active").order_by("user__last_name")
            ]
        )
        context["ineligible_pi"] = not check_if_pis_eligible([project_obj.pi.username]).get(
            project_obj.pi.username, True
        )
        context["formset"] = []
        allocation_data = self.get_allocation_data(project_obj)
        if allocation_data:
            formset = formset_factory(ProjectReviewAllocationForm, max_num=len(allocation_data))
            formset = formset(initial=allocation_data, prefix="allocationform")
            context["formset"] = formset

        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))
        project_review_form = ProjectReviewForm(request.POST)

        if not project_review_form.is_valid():
            messages.error(request, "There was an error in processing your project review.")
            errors = project_review_form.errors.get("__all__")
            if errors:
                for error in errors:
                    messages.error(request, error)
            return HttpResponseRedirect(reverse("project-review", kwargs={"pk": project_obj.pk}))

        allocation_renewals = []
        allocation_data = self.get_allocation_data(project_obj)
        if allocation_data:
            formset = formset_factory(ProjectReviewAllocationForm, max_num=len(allocation_data))
            formset = formset(request.POST, initial=allocation_data, prefix="allocationform")

            if not formset.is_valid():
                logger.error(
                    f"There was an error submitting allocation renewals for PI "
                    f"{project_obj.pi.username} (project pk={project_obj.pk}) "
                    f"Errors: {formset.errors}"
                )
                messages.error(request, "There was an error submitting your allocation renewals.")
                return redirect(project_obj)

            allocation_status_choice = AllocationStatusChoice.objects.get(name="Renewal Requested")
            for form in formset:
                data = form.cleaned_data
                if data.get("renew"):
                    allocation_renewals.append(str(data.get("pk")))
                    allocation = Allocation.objects.get(pk=data.get("pk"))
                    allocation.status = allocation_status_choice
                    allocation.save()

        form_data = project_review_form.cleaned_data
        project_updates = form_data.get("project_updates")
        if form_data.get("no_project_updates"):
            project_updates = "No new project updates."

        ProjectReview.objects.create(
            project=project_obj,
            project_updates=project_updates,
            allocation_renewals=",".join(allocation_renewals),
            status=ProjectReviewStatusChoice.objects.get(name="Pending"),
        )

        project_obj.force_review = False
        project_obj.status = ProjectStatusChoice.objects.get(name="Review Pending")
        project_obj.save()

        domain_url = get_domain_url(self.request)
        url = "{}{}".format(domain_url, reverse("project-review-list"))

        if EMAIL_ENABLED:
            send_email_template(
                "New project renewal has been submitted",
                "email/new_project_renewal.txt",
                {"url": url, "project_title": project_obj.title, "project_id": project_obj.pk},
                [EMAIL_ALERTS_EMAIL_ADDRESS],
            )

        if SLACK_MESSAGING_ENABLED:
            text = (
                f'A new renewal request for project "{project_obj.title}" with id '
                f"{project_obj.pk} has been submitted. You can view it here: {url}"
            )
            send_message(text)

        logger.info(f"User {request.user.username} submitted a project review (project pk={project_obj.pk})")

        messages.success(request, "Project review submitted.")
        return redirect(project_obj)


class ProjectReviewListView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "project/project_review_list.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""

        if self.request.user.is_superuser:
            return True

        if self.request.user.has_perm("project.can_review_pending_projects"):
            return True

        messages.error(self.request, "You do not have permission to review pending project reviews/requests.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        contacted_pis = {}

        project_review_objs = (
            ProjectReview.objects.filter(status__name__in=["Pending", "Contacted By Admin"])
            .select_related("status", "project", "project__pi")
            .order_by("created")
        )
        context["project_reviews"] = self._build_contacted_list(
            project_review_objs, lambda r: r.project.pi, contacted_pis
        )

        context["pi_eligibilities"] = check_if_pis_eligible(
            {project_review.project.pi.username for project_review in project_review_objs}
        )

        project_requests = (
            Project.objects.filter(status__name__in=["Waiting For Admin Approval", "Contacted By Admin"])
            .select_related("status", "requestor", "pi", "type")
            .order_by("created")
        )
        context["project_requests"] = self._build_contacted_list(project_requests, lambda p: p.pi, contacted_pis)
        context["contacted_pis"] = contacted_pis

        pis = {project.pi for project in project_requests} | {
            project_review.project.pi for project_review in project_review_objs
        }
        pi_project_objs = (
            Project.objects.filter(
                Q(
                    pi__in=pis,
                    status__name__in=["Active", "Waiting For Admin Approval", "Contacted By Admin", "Review Pending"],
                )
                | Q(
                    pi__in=pis,
                    status__name="Expired",
                    end_date__gt=datetime.date.today() - datetime.timedelta(days=PROJECT_DAYS_TO_REVIEW_AFTER_EXPIRING),
                )
            )
            .select_related("status", "pi", "requestor")
            .order_by("status__name")
        )
        context["pi_projects"] = [
            {
                "pk": p.pk,
                "title": p.title,
                "pi": p.pi.username,
                "description": p.description,
                "status": p.status.name,
                "display": "false",
            }
            for p in pi_project_objs
        ]

        context["EMAIL_ENABLED"] = EMAIL_ENABLED
        return context

    def _build_contacted_list(self, objs, get_pi, contacted_pis):
        result = []
        for obj in objs:
            pi_username = get_pi(obj).username
            contacted_pis.setdefault(pi_username, False)
            entry = {"info": obj, "contacted_by": ""}
            if obj.status.name == "Contacted By Admin":
                entry["contacted_by"] = obj.history.first().history_user
                contacted_pis[pi_username] = True
            result.append(entry)
        return result


class ProjectReviewCompleteView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Currently not in use."""

    login_url = "/"

    def test_func(self):
        """UserPassesTestMixin Tests"""

        if self.request.user.is_superuser:
            return True

        if self.request.user.has_perm("project.can_review_pending_projects"):
            return True

        messages.error(self.request, "You do not have permission to mark a pending project review as completed.")

    def get(self, request, project_review_pk):
        project_review_obj = get_object_or_404(ProjectReview, pk=project_review_pk)

        project_review_status_completed_obj = ProjectReviewStatusChoice.objects.get(name="Completed")
        project_review_obj.status = project_review_status_completed_obj
        if project_review_obj.project.project_needs_review:
            project_review_obj.project.project_needs_review = False
            project_review_obj.save()

        messages.success(request, "Project review for {} has been completed".format(project_review_obj.project.title))

        return HttpResponseRedirect(reverse("project-review-list"))


class ProjectReviewEmailView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    form_class = ProjectReviewEmailForm
    template_name = "project/project_review_email.html"
    login_url = "/"

    def test_func(self):
        """UserPassesTestMixin Tests"""

        if not EMAIL_ENABLED:
            messages.error(self.request, "Emails are not enabled.")
            return False

        if self.request.user.is_superuser:
            return True

        if self.request.user.has_perm("project.can_review_pending_projects"):
            return True

        messages.error(self.request, "You do not have permission to send email for a pending project review.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        project_review_obj = get_object_or_404(ProjectReview, pk=pk)
        context["project_review"] = project_review_obj

        return context

    def get_form(self, form_class=None):
        """Return an instance of the form to be used in this view."""
        if form_class is None:
            form_class = self.get_form_class()
        return form_class(self.kwargs.get("pk"), self.request.user, **self.get_form_kwargs())

    def form_valid(self, form):
        pk = self.kwargs.get("pk")
        project_review_obj = get_object_or_404(ProjectReview, pk=pk)
        form_data = form.cleaned_data

        project_review_status_obj = ProjectReviewStatusChoice.objects.get(name="Contacted By Admin")
        project_review_obj.status = project_review_status_obj
        project_review_obj.save()

        receiver_list = [project_review_obj.project.pi.email]
        cc = form_data.get("cc").strip()
        if cc:
            cc = cc.split(",")
        else:
            cc = []

        send_email(
            f"Follow-up on Renewal for Project {project_review_obj.project.title}",
            form_data.get("email_body"),
            EMAIL_TICKET_SYSTEM_ADDRESS,
            receiver_list,
            cc,
        )
        success_text = "Email sent to {} {} ({}).".format(
            project_review_obj.project.pi.first_name,
            project_review_obj.project.pi.last_name,
            project_review_obj.project.pi.username,
        )
        if cc:
            success_text += " CCed: {}".format(", ".join(cc))

        messages.success(self.request, success_text)
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("project-review-list")


class ProjectNoteCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = ProjectUserMessage
    fields = "__all__"
    template_name = "project/project_note_create.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""

        if self.request.user.is_superuser:
            return True
        else:
            messages.error(self.request, "You do not have permission to add project notes.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        project_obj = get_object_or_404(Project, pk=pk)
        context["project"] = project_obj
        return context

    def get_initial(self):
        initial = super().get_initial()
        pk = self.kwargs.get("pk")
        project_obj = get_object_or_404(Project, pk=pk)
        author = self.request.user
        initial["project"] = project_obj
        initial["author"] = author
        return initial

    def get_form(self, form_class=None):
        """Return an instance of the form to be used in this view."""
        form = super().get_form(form_class)
        form.fields["project"].widget = forms.HiddenInput()
        form.fields["author"].widget = forms.HiddenInput()
        form.order_fields(["project", "author", "message", "is_private"])
        return form

    def get_success_url(self):
        logger.info(f"Admin {self.request.user.username} created a project attribute (pk={self.kwargs.get('pk')})")
        return reverse("project-detail", kwargs={"pk": self.kwargs.get("pk")})


class ProjectAttributeCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = ProjectAttribute
    form_class = ProjectAttributeAddForm
    template_name = "project/project_attribute_create.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""

        user = self.request.user
        if user.is_superuser or user.has_perm("project.add_projectattribute"):
            return True

        messages.error(self.request, "You do not have permission to add project attributes.")

    def get_initial(self):
        initial = super().get_initial()
        pk = self.kwargs.get("pk")
        initial["project"] = get_object_or_404(Project, pk=pk)
        initial["user"] = self.request.user
        return initial

    def get_form(self, form_class=None):
        """Return an instance of the form to be used in this view."""
        form = super().get_form(form_class)
        form.fields["project"].widget = forms.HiddenInput()
        return form

    def get_context_data(self, *args, **kwargs):
        pk = self.kwargs.get("pk")
        context = super().get_context_data(*args, **kwargs)
        context["project"] = get_object_or_404(Project, pk=pk)
        return context

    def get_success_url(self):
        logger.info(
            f"Admin {self.request.user.username} created a project attribute (project pk={self.object.project_id})"
        )
        create_admin_action_for_creation(
            self.request.user, self.object, get_object_or_404(Project, pk=self.object.project_id)
        )
        return reverse("project-detail", kwargs={"pk": self.object.project_id})


class ProjectAttributeDeleteView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    model = ProjectAttribute
    form_class = ProjectAttributeDeleteForm
    template_name = "project/project_attribute_delete.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""

        user = self.request.user
        if user.is_superuser or user.has_perm("project.delete_projectattribute"):
            return True

        messages.error(self.request, "You do not have permission to add project attributes.")

    def get_avail_attrs(self, project_obj):
        avail_attrs = ProjectAttribute.objects.select_related("proj_attr_type").filter(project=project_obj)
        if not self.request.user.is_superuser and not self.request.user.has_perm("project.delete_projectattribute"):
            avail_attrs = avail_attrs.filter(proj_attr_type__is_private=False)
        avail_attrs_dicts = [
            {"pk": attr.pk, "selected": False, "name": str(attr.proj_attr_type), "value": attr.value}
            for attr in avail_attrs
        ]

        return avail_attrs_dicts

    def get(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        project_obj = get_object_or_404(Project, pk=pk)

        project_attributes_to_delete = self.get_avail_attrs(project_obj)
        context = {}

        if project_attributes_to_delete:
            formset = formset_factory(ProjectAttributeDeleteForm, max_num=len(project_attributes_to_delete))
            formset = formset(initial=project_attributes_to_delete, prefix="attributeform")
            context["formset"] = formset
        context["project"] = project_obj
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        attr_to_delete = self.get_avail_attrs(pk)

        formset = formset_factory(ProjectAttributeDeleteForm, max_num=len(attr_to_delete))
        formset = formset(request.POST, initial=attr_to_delete, prefix="attributeform")

        attributes_deleted_count = 0

        if formset.is_valid():
            for form in formset:
                form_data = form.cleaned_data
                if form_data["selected"]:
                    attributes_deleted_count += 1

                    proj_attr = ProjectAttribute.objects.get(pk=form_data["pk"])

                    proj_attr.delete()

                    create_admin_action_for_deletion(self.request.user, proj_attr, get_object_or_404(Project, pk=pk))

            logger.info(
                f"Admin {self.request.user.username} deleted {attributes_deleted_count} project "
                f"attributes (project pk={pk})"
            )

            messages.success(request, "Deleted {} attributes from project.".format(attributes_deleted_count))
        else:
            for error in formset.errors:
                messages.error(request, error)

        return HttpResponseRedirect(reverse("project-detail", kwargs={"pk": pk}))


class ProjectAttributeUpdateView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "project/project_attribute_update.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        user = self.request.user
        if user.is_superuser or user.has_perm("project.change_projectattribute"):
            return True

    def get(self, request, *args, **kwargs):
        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))
        project_attribute_pk = self.kwargs.get("project_attribute_pk")

        if project_obj.projectattribute_set.filter(pk=project_attribute_pk).exists():
            project_attribute_obj = project_obj.projectattribute_set.get(pk=project_attribute_pk)

            project_attribute_update_form = ProjectAttributeUpdateForm(
                initial={
                    "pk": self.kwargs.get("project_attribute_pk"),
                    "name": project_attribute_obj,
                    "value": project_attribute_obj.value,
                    "type": project_attribute_obj.proj_attr_type,
                }
            )

            context = {}
            context["project_obj"] = project_obj
            context["project_attribute_update_form"] = project_attribute_update_form
            context["project_attribute_obj"] = project_attribute_obj

            return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))
        project_attribute_pk = self.kwargs.get("project_attribute_pk")

        if project_obj.projectattribute_set.filter(pk=project_attribute_pk).exists():
            project_attribute_obj = project_obj.projectattribute_set.get(pk=project_attribute_pk)

            if project_obj.status.name not in [
                "Active",
                "New",
                "Waiting For Admin Approval",
                "Contacted By Admin",
                "Renewal Requested",
            ]:
                messages.error(
                    request, f"You cannot update an attribute in a project with status {project_obj.status.name}."
                )
                return HttpResponseRedirect(
                    reverse(
                        "project-attribute-update",
                        kwargs={"pk": project_obj.pk, "project_attribute_pk": project_attribute_obj.pk},
                    )
                )

            project_attribute_update_form = ProjectAttributeUpdateForm(
                request.POST,
                initial={
                    "pk": self.kwargs.get("project_attribute_pk"),
                },
            )

            if project_attribute_update_form.is_valid():
                form_data = project_attribute_update_form.cleaned_data
                logger.info(f"Admin {request.user.username} updated a project attribute (project pk={project_obj.pk})")
                create_admin_action(
                    request.user, {"value": form_data.get("new_value")}, project_obj, project_attribute_obj
                )
                project_attribute_obj.value = form_data.get("new_value")
                project_attribute_obj.save()

                messages.success(request, "Attribute Updated.")
                return redirect(project_obj)
            else:
                for error in project_attribute_update_form.errors.values():
                    messages.error(request, error)
                return HttpResponseRedirect(
                    reverse(
                        "project-attribute-update",
                        kwargs={"pk": project_obj.pk, "project_attribute_pk": project_attribute_obj.pk},
                    )
                )


class ProjectAdminCommentCreateView(SuccessMessageMixin, LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = ProjectAdminComment
    fields = ["project", "author", "comment"]
    template_name = "project/project_admin_comment_create.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        return self.request.user.is_superuser

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        project_obj = get_object_or_404(Project, pk=pk)
        context["project"] = project_obj
        return context

    def get_initial(self):
        initial = super().get_initial()
        pk = self.kwargs.get("pk")
        project_obj = get_object_or_404(Project, pk=pk)
        author = self.request.user
        initial["project"] = project_obj
        initial["author"] = author
        return initial

    def get_form(self, form_class=None):
        """Return an instance of the form to be used in this view."""
        form = super().get_form(form_class)
        form.fields["project"].widget = forms.HiddenInput()
        form.fields["author"].widget = forms.HiddenInput()
        form.order_fields(["project", "author", "comment"])
        return form

    def get_success_url(self):
        return self.object.project.get_absolute_url()


class ProjectDeniedListView(LoginRequiredMixin, ListView):
    model = Project
    template_name = "project/project_denied_list.html"
    prefetch_related = [
        "pi",
        "status",
        "field_of_science",
    ]
    context_object_name = "project_list"
    paginate_by = 10

    def get_queryset(self):
        order_by = self.request.GET.get("order_by", "id")
        direction = self.request.GET.get("direction", "")
        if order_by != "name":
            if direction == "des":
                direction = "-"
            order_by = direction + order_by

        project_search_form = ProjectSearchForm(self.request.GET)

        if project_search_form.is_valid():
            data = project_search_form.cleaned_data
            if data.get("show_all_projects") and (
                self.request.user.is_superuser or self.request.user.has_perm("project.can_view_all_projects")
            ):
                projects = (
                    Project.objects.prefetch_related(
                        "pi",
                        "field_of_science",
                        "status",
                    )
                    .filter(
                        status__name__in=[
                            "Denied",
                            "Renewal Denied",
                        ]
                    )
                    .order_by(order_by)
                )
            else:
                projects = (
                    Project.objects.prefetch_related(
                        "pi",
                        "field_of_science",
                        "status",
                    )
                    .filter(
                        Q(
                            status__name__in=[
                                "Denied",
                                "Renewal Denied",
                            ]
                        )
                        & Q(projectuser__user=self.request.user)
                        & Q(projectuser__status__name="Active")
                    )
                    .order_by(order_by)
                )

            # Last Name
            if data.get("last_name"):
                projects = projects.filter(pi__last_name__icontains=data.get("last_name"))

            # Username
            if data.get("username"):
                projects = projects.filter(pi__username__icontains=data.get("username"))

            # Field of Science
            if data.get("field_of_science"):
                projects = projects.filter(field_of_science__description__icontains=data.get("field_of_science"))

        else:
            projects = (
                Project.objects.prefetch_related(
                    "pi",
                    "field_of_science",
                    "status",
                )
                .filter(
                    Q(
                        status__name__in=[
                            "Denied",
                            "Renewal Denied",
                        ]
                    )
                    & Q(projectuser__user=self.request.user)
                    & Q(projectuser__status__name="Active")
                )
                .order_by(order_by)
            )

        return projects

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        projects_count = self.get_queryset().count()
        context["projects_count"] = projects_count
        context["expand"] = False

        project_search_form = ProjectSearchForm(self.request.GET)
        if project_search_form.is_valid():
            context["project_search_form"] = project_search_form
            data = project_search_form.cleaned_data
            filter_parameters = ""
            for key, value in data.items():
                if value:
                    if isinstance(value, list):
                        for ele in value:
                            filter_parameters += "{}={}&".format(key, ele)
                    else:
                        filter_parameters += "{}={}&".format(key, value)
        else:
            filter_parameters = None
            context["project_search_form"] = ProjectSearchForm()

        order_by = self.request.GET.get("order_by")
        if order_by:
            direction = self.request.GET.get("direction")
            filter_parameters_with_order_by = filter_parameters + "order_by=%s&direction=%s&" % (order_by, direction)
        else:
            filter_parameters_with_order_by = filter_parameters

        if filter_parameters:
            context["expand_accordion"] = "show"

        context["filter_parameters"] = filter_parameters
        context["filter_parameters_with_order_by"] = filter_parameters_with_order_by

        project_list = context.get("project_list")
        paginator = Paginator(project_list, self.paginate_by)

        page = self.request.GET.get("page")

        try:
            project_list = paginator.page(page)
        except PageNotAnInteger:
            project_list = paginator.page(1)
        except EmptyPage:
            project_list = paginator.page(paginator.num_pages)

        return context


class ProjectRequestEmailMixin:
    def send_request_email(self, project_obj, subject, template, only_project_managers=False):
        domain_url = get_domain_url(self.request)
        template_context = {
            "project_title": project_obj.title,
            "project_url": "{}{}".format(domain_url, reverse("project-detail", kwargs={"pk": project_obj.pk})),
            "signature": EMAIL_SIGNATURE,
            "help_email": EMAIL_TICKET_SYSTEM_ADDRESS,
            "center_name": EMAIL_CENTER_NAME,
        }
        send_email_template(
            subject,
            template,
            template_context,
            get_project_user_emails(project_obj, only_project_managers),
            EMAIL_TICKET_SYSTEM_ADDRESS,
        )


class ProjectActivateRequestView(LoginRequiredMixin, UserPassesTestMixin, ProjectRequestEmailMixin, View):
    login_url = "/"

    def test_func(self):
        """UserPassesTestMixin Tests"""

        if not self.request.user.is_superuser:
            if not self.request.user.has_perm("project.can_review_pending_projects"):
                return False

        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))
        if project_obj.status.name not in [
            "Waiting For Admin Approval",
            "Contacted By Admin",
        ]:
            messages.error(self.request, f'You cannot approve a project with status "{project_obj.status.name}"')
            return False

        return True

    def get(self, request, pk):
        project_obj = get_object_or_404(Project, pk=pk)
        project_status_obj = ProjectStatusChoice.objects.get(name="Active")

        create_admin_action(request.user, {"status": project_status_obj}, project_obj)

        project_obj.status = project_status_obj
        project_obj.save()

        project_activate.send(sender=self.__class__, project_pk=project_obj.pk)
        messages.success(request, "Project request for {} has been APPROVED".format(project_obj.title))

        if EMAIL_ENABLED:
            self.send_request_email(
                project_obj, "Your Project Request Was Approved", "email/project_request_approved.txt"
            )

        logger.info(f"Admin {request.user.username} approved a project request (project pk={project_obj.pk})")
        return redirect("project-review-list")


class ProjectDenyRequestView(LoginRequiredMixin, UserPassesTestMixin, ProjectRequestEmailMixin, View):
    login_url = "/"

    def test_func(self):
        """UserPassesTestMixin Tests"""

        if not self.request.user.is_superuser:
            if not self.request.user.has_perm("project.can_review_pending_projects"):
                return False

        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))
        if project_obj.status.name not in [
            "Waiting For Admin Approval",
            "Contacted By Admin",
        ]:
            messages.error(self.request, f'You cannot deny a project with status "{project_obj.status.name}"')
            return False

        return True

    def get(self, request, pk):
        project_obj = get_object_or_404(Project, pk=pk)
        project_status_obj = ProjectStatusChoice.objects.get(name="Denied")

        with transaction.atomic():
            create_admin_action(request.user, {"status": project_status_obj}, project_obj)

            project_obj.status = project_status_obj
            project_obj.save()

            allocation_status_denied = AllocationStatusChoice.objects.get(name="Denied")
            project_obj.allocation_set.filter(status__name__in=["Active", "New", "Renewal Requested"]).update(
                status=allocation_status_denied
            )

            allocation_status_declined = AllocationStatusChoice.objects.get(name="Payment Declined")
            project_obj.allocation_set.filter(status__name__in=["Payment Requested", "Payment Pending", "Paid"]).update(
                status=allocation_status_declined
            )

        messages.success(request, "Project request for {} has been DENIED".format(project_obj.title))

        if EMAIL_ENABLED:
            self.send_request_email(
                project_obj,
                "Your Project Request Was Denied",
                "email/project_request_denied.txt",
                only_project_managers=True,
            )

        logger.info(f"Admin {request.user.username} denied a project request (project pk={project_obj.pk})")
        return redirect("project-review-list")


class ProjectReviewApproveView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        """UserPassesTestMixin Tests"""

        if not self.request.user.is_superuser:
            if not self.request.user.has_perm("project.can_review_pending_projects"):
                return False

        project_review_obj = get_object_or_404(ProjectReview, pk=self.kwargs.get("pk"))
        if project_review_obj.status.name not in [
            "Pending",
            "Contacted By Admin",
        ]:
            messages.error(
                self.request, f'You cannot approve a project review with status "{project_review_obj.status.name}"'
            )
            return False

        return True

    def get(self, request, pk):
        project_review_obj = get_object_or_404(ProjectReview, pk=pk)
        project_review_status_obj = ProjectReviewStatusChoice.objects.get(name="Approved")
        project_obj = project_review_obj.project
        project_status_obj = ProjectStatusChoice.objects.get(name="Active")

        end_date = get_new_end_date_from_list(
            project_obj.get_env.get("expiry_dates"), buffer_days=PROJECT_END_DATE_CARRYOVER_DAYS
        )

        if end_date is None:
            logger.error(
                f"New end date for project {project_obj.title} was set to None with project "
                f"review creation date {project_review_obj.created.date()} during project "
                f"review approval"
            )
            messages.error(request, "Something went wrong while approving the review.")
            return redirect("project-review-list")

        with transaction.atomic():
            project_obj.end_date = end_date

            create_admin_action(request.user, {"status": project_status_obj}, project_obj)

            project_review_obj.status = project_review_status_obj
            project_obj.status = project_status_obj
            project_review_obj.save()
            project_obj.save()

        messages.success(request, "Project review for {} has been APPROVED".format(project_obj.title))

        if EMAIL_ENABLED:
            domain_url = get_domain_url(self.request)
            template_context = {
                "project_title": project_obj.title,
                "project_url": "{}{}".format(domain_url, reverse("project-detail", kwargs={"pk": project_obj.pk})),
                "signature": EMAIL_SIGNATURE,
                "help_email": EMAIL_TICKET_SYSTEM_ADDRESS,
                "center_name": EMAIL_CENTER_NAME,
            }
            send_email_template(
                "Your Project Renewal Was Approved",
                "email/project_renewal_approved.txt",
                template_context,
                get_project_user_emails(project_obj),
                EMAIL_TICKET_SYSTEM_ADDRESS,
            )

        logger.info(f"Admin {request.user.username} approved a project renewal request (project pk={project_obj.pk})")

        project_review_approved.send(sender=self.__class__, project_review_pk=project_review_obj.pk)

        return redirect("project-review-list")


class ProjectReviewDenyView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        """UserPassesTestMixin Tests"""

        if not self.request.user.is_superuser:
            if not self.request.user.has_perm("project.can_review_pending_projects"):
                return False

        project_review_obj = get_object_or_404(ProjectReview, pk=self.kwargs.get("pk"))
        if project_review_obj.status.name not in [
            "Pending",
            "Contacted By Admin",
        ]:
            messages.error(
                self.request, f'You cannot deny a project review with status "{project_review_obj.status.name}"'
            )
            return False

        return True

    def get(self, request, pk):
        project_review_obj = get_object_or_404(ProjectReview, pk=pk)
        project_review_status_obj = ProjectReviewStatusChoice.objects.get(name="Denied")
        project_obj = project_review_obj.project
        project_status_obj = ProjectStatusChoice.objects.get(name="Renewal Denied")

        with transaction.atomic():
            create_admin_action(request.user, {"status": project_status_obj}, project_obj)

            project_review_obj.status = project_review_status_obj
            project_obj.status = project_status_obj

            allocation_renewals = project_obj.allocation_set.filter(status__name="Renewal Requested")
            if allocation_renewals:
                allocation_active_status_choice = AllocationStatusChoice.objects.get(name="Active")
                allocation_expired_status_choice = AllocationStatusChoice.objects.get(name="Expired")
                for allocation in allocation_renewals:
                    if allocation.end_date < datetime.date.today():
                        allocation.status = allocation_expired_status_choice
                        allocation_expire.send(sender=ProjectReviewDenyView, allocation_pk=allocation.pk)
                    else:
                        allocation.status = allocation_active_status_choice
                    allocation.save()

            project_review_obj.save()
            project_obj.save()

        messages.success(request, "Project review for {} has been DENIED".format(project_obj.title))

        if EMAIL_ENABLED:
            domain_url = get_domain_url(self.request)
            not_renewed_allocation_urls = (
                [
                    "{}{}".format(domain_url, reverse("allocation-detail", kwargs={"pk": pk}))
                    for pk in project_review_obj.allocation_renewals.split(",")
                ]
                if project_review_obj.allocation_renewals
                else []
            )

            template_context = {
                "project_title": project_obj.title,
                "project_url": "{}{}".format(domain_url, reverse("project-detail", kwargs={"pk": project_obj.pk})),
                "help_email": EMAIL_TICKET_SYSTEM_ADDRESS,
                "center_name": EMAIL_CENTER_NAME,
                "not_renewed_allocation_urls": not_renewed_allocation_urls,
                "signature": EMAIL_SIGNATURE,
            }

            send_email_template(
                "Your Project Renewal Was Denied",
                "email/project_renewal_denied.txt",
                template_context,
                get_project_user_emails(project_obj, True),
                EMAIL_TICKET_SYSTEM_ADDRESS,
            )

        logger.info(f"Admin {request.user.username} denied a project renewal request (project pk={project_obj.pk})")
        return redirect("project-review-list")


class ProjectReviewInfoView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "project/project_review_info.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""

        if not self.request.user.is_superuser:
            if not self.request.user.has_perm("project.can_review_pending_projects"):
                return False

        project_review_obj = get_object_or_404(ProjectReview, pk=self.kwargs.get("pk"))
        if project_review_obj.status.name not in ["Pending", "Contacted By Admin"]:
            messages.error(
                self.request, f'You cannot view a project review\'s info with status "{project_review_obj.status.name}"'
            )
            return False

        return True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        context["project_review"] = get_object_or_404(ProjectReview, pk=pk)

        return context


class ProjectRequestEmailView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    form_class = ProjectRequestEmailForm
    template_name = "project/project_request_email.html"
    login_url = "/"

    def test_func(self):
        """UserPassesTestMixin Tests"""

        if not EMAIL_ENABLED:
            messages.error(self.request, "Emails are not enabled.")
            return False

        if self.request.user.is_superuser:
            return True

        if self.request.user.has_perm("project.can_review_pending_projects"):
            return True

        messages.error(self.request, "You do not have permission to send email for a pending project request.")
        return False

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        project_obj = get_object_or_404(Project, pk=pk)
        context["project"] = project_obj

        return context

    def get_form(self, form_class=None):
        """Return an instance of the form to be used in this view."""
        if form_class is None:
            form_class = self.get_form_class()
        return form_class(self.kwargs.get("pk"), self.request.user, **self.get_form_kwargs())

    def form_valid(self, form):
        project_obj = get_object_or_404(Project, pk=self.kwargs.get("pk"))
        form_data = form.cleaned_data

        project_status_obj = ProjectStatusChoice.objects.get(name="Contacted By Admin")
        create_admin_action(self.request.user, {"status": project_status_obj}, project_obj)

        project_obj.status = project_status_obj
        project_obj.save()

        cc = [email.strip() for email in form_data.get("cc", "").split(",") if email.strip()]

        send_email(
            f"Follow-up on Project {project_obj.title}",
            form_data.get("email_body"),
            EMAIL_TICKET_SYSTEM_ADDRESS,
            [project_obj.requestor.email],
            cc,
        )

        success_text = "Email sent to {} {} ({}).".format(
            project_obj.requestor.first_name, project_obj.requestor.last_name, project_obj.requestor.username
        )
        if cc:
            success_text += " CCed: {}".format(", ".join(cc))

        messages.success(self.request, success_text)
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("project-review-list")


class ProjectRequestAccessEmailView(LoginRequiredMixin, View):
    def post(self, request):
        project_obj = get_object_or_404(Project, pk=request.POST.get("project_pk"))
        if project_obj.private:
            logger.warning(
                f"User {request.user.username} attempted to request access to a private project (pk={project_obj.pk})"
            )
            return redirect("project-list")

        domain_url = get_domain_url(self.request)
        project_url = "{}{}".format(domain_url, reverse("project-detail", kwargs={"pk": project_obj.pk}))

        if EMAIL_ENABLED:
            send_email_template(
                "Add User to Project Request",
                "email/project_add_user_request.txt",
                {
                    "center_name": EMAIL_CENTER_NAME,
                    "user": request.user,
                    "project_title": project_obj.title,
                    "project_url": project_url,
                    "help_email": EMAIL_TICKET_SYSTEM_ADDRESS,
                    "signature": EMAIL_SIGNATURE,
                },
                [project_obj.pi.email],
                EMAIL_TICKET_SYSTEM_ADDRESS,
            )
            logger.info(
                f"User {request.user.username} sent an email to {project_obj.pi.email} requesting "
                f"access to their project (project pk={project_obj.pk})"
            )
        else:
            logger.warning("Email has not been enabled")
            return redirect("project-list")

        return redirect("project-list")


class PiProjectsPartialView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "project/project_review_modal_content.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""

        if self.request.user.is_superuser:
            return True

        if self.request.user.has_perm("project.can_review_pending_projects"):
            return True

        messages.error(self.request, "You do not have permission to review pending project reviews/requests.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pi_username = self.request.GET.get("pi")
        pi_project_objs = (
            Project.objects.filter(pi__username=pi_username)
            .filter(
                Q(status__name__in=["Active", "Waiting For Admin Approval", "Contacted By Admin", "Review Pending"])
                | Q(
                    status__name="Expired",
                    end_date__gt=datetime.date.today() - datetime.timedelta(days=PROJECT_DAYS_TO_REVIEW_AFTER_EXPIRING),
                )
            )
            .order_by("status__name")
        )
        context["projects"] = pi_project_objs
        return context


def project_review_stats(request):
    current_date = datetime.date.today()
    days_prior = current_date - datetime.timedelta(days=PROJECT_DAYS_TO_REVIEW_AFTER_EXPIRING)
    days_after = current_date + datetime.timedelta(days=PROJECT_DAYS_TO_REVIEW_BEFORE_EXPIRING)
    project_status_counts = Counter(
        Project.objects.filter(requires_review=True, end_date__range=(days_prior, days_after))
        .exclude(status__name__in=["Archived", "Denied"])
        .select_related("status")
        .values_list("status__name", flat=True)
    )

    data = []
    for status_name, count in project_status_counts.items():
        data.append({"name": status_name, "total": count})
        print(data)
    return JsonResponse({"data": data})
