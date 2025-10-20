# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import datetime
import logging
from datetime import date

from dateutil.relativedelta import relativedelta
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Q
from django.db.models.query import QuerySet
from django.forms import formset_factory
from django.http import HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.utils.html import format_html
from django.views import View
from django.views.generic import ListView, TemplateView
from django.views.generic.edit import CreateView, FormView, UpdateView

from coldfront.config.core import ALLOCATION_EULA_ENABLE
from coldfront.core.allocation.forms import (
    AllocationAccountForm,
    AllocationAddUserForm,
    AllocationAddUserFormset,
    AllocationAttributeChangeForm,
    AllocationAttributeCreateForm,
    AllocationAttributeDeleteForm,
    AllocationAttributeEditForm,
    AllocationAttributeUpdateForm,
    AllocationChangeForm,
    AllocationChangeNoteForm,
    AllocationForm,
    AllocationInvoiceNoteDeleteForm,
    AllocationInvoiceUpdateForm,
    AllocationRemoveUserForm,
    AllocationReviewUserForm,
    AllocationSearchForm,
    AllocationUpdateForm,
    AllocationUserUpdateForm,
)
from coldfront.core.allocation.models import (
    Allocation,
    AllocationAccount,
    AllocationAttribute,
    AllocationAttributeChangeRequest,
    AllocationAttributeType,
    AllocationChangeRequest,
    AllocationChangeStatusChoice,
    AllocationPermission,
    AllocationStatusChoice,
    AllocationUser,
    AllocationUserNote,
    AllocationUserStatusChoice,
)
from coldfront.core.allocation.signals import (
    allocation_activate,
    allocation_activate_user,
    allocation_attribute_changed,
    allocation_change_approved,
    allocation_change_created,
    allocation_change_user_role,
    allocation_disable,
    allocation_new,
    allocation_remove_user,
    visit_allocation_detail,
)
from coldfront.core.allocation.utils import (
    check_if_roles_are_enabled,
    create_admin_action,
    create_admin_action_for_creation,
    create_admin_action_for_deletion,
    generate_guauge_data_from_usage,
    get_user_resources,
    send_added_user_email,
    send_removed_user_email,
)
from coldfront.core.project.models import (
    Project,
    ProjectPermission,
    ProjectUser,
    ProjectUserStatusChoice,
)
from coldfront.core.resource.models import Resource
from coldfront.core.utils.common import get_domain_url, import_from_settings
from coldfront.core.utils.groups import check_if_groups_in_review_groups
from coldfront.core.utils.mail import (
    build_link,
    send_allocation_admin_email,
    send_allocation_customer_email,
    send_allocation_eula_customer_email,
    send_email_template,
)

ALLOCATION_ENABLE_ALLOCATION_RENEWAL = import_from_settings("ALLOCATION_ENABLE_ALLOCATION_RENEWAL", True)
ALLOCATION_DEFAULT_ALLOCATION_LENGTH = import_from_settings("ALLOCATION_DEFAULT_ALLOCATION_LENGTH", 365)
ALLOCATION_ENABLE_CHANGE_REQUESTS_BY_DEFAULT = import_from_settings(
    "ALLOCATION_ENABLE_CHANGE_REQUESTS_BY_DEFAULT", True
)
ALLOCATION_DAYS_TO_REVIEW_BEFORE_EXPIRING = import_from_settings("ALLOCATION_DAYS_TO_REVIEW_BEFORE_EXPIRING", 30)
ALLOCATION_DAYS_TO_REVIEW_AFTER_EXPIRING = import_from_settings("ALLOCATION_DAYS_TO_REVIEW_AFTER_EXPIRING", 60)
ALLOCATION_ATTRIBUTE_IDENTIFIERS = import_from_settings("ALLOCATION_ATTRIBUTE_IDENTIFIERS", [])

EMAIL_TICKET_SYSTEM_ADDRESS = import_from_settings("EMAIL_TICKET_SYSTEM_ADDRESS", "")
EMAIL_RESOURCE_EMAIL_TEMPLATES = import_from_settings("EMAIL_RESOURCE_EMAIL_TEMPLATES", {})

PROJECT_ENABLE_PROJECT_REVIEW = import_from_settings("PROJECT_ENABLE_PROJECT_REVIEW", False)
INVOICE_ENABLED = import_from_settings("INVOICE_ENABLED", False)
if INVOICE_ENABLED:
    INVOICE_DEFAULT_STATUS = import_from_settings("INVOICE_DEFAULT_STATUS", "Pending Payment")

ALLOCATION_ACCOUNT_ENABLED = import_from_settings("ALLOCATION_ACCOUNT_ENABLED", False)
ALLOCATION_ACCOUNT_MAPPING = import_from_settings("ALLOCATION_ACCOUNT_MAPPING", {})

EMAIL_ALLOCATION_EULA_IGNORE_OPT_OUT = import_from_settings("EMAIL_ALLOCATION_EULA_IGNORE_OPT_OUT", False)
EMAIL_ALLOCATION_EULA_CONFIRMATIONS = import_from_settings("EMAIL_ALLOCATION_EULA_CONFIRMATIONS", False)
EMAIL_ALLOCATION_EULA_CONFIRMATIONS_CC_MANAGERS = import_from_settings(
    "EMAIL_ALLOCATION_EULA_CONFIRMATIONS_CC_MANAGERS", False
)
EMAIL_ALLOCATION_EULA_INCLUDE_ACCEPTED_EULA = import_from_settings("EMAIL_ALLOCATION_EULA_INCLUDE_ACCEPTED_EULA", False)

SLACK_MESSAGING_ENABLED = import_from_settings("SLACK_MESSAGING_ENABLED", False)

ALLOCATION_REMOVAL_REQUESTS_ALLOWED = import_from_settings("ALLOCATION_REMOVAL_REQUESTS_ALLOWED", [""])

logger = logging.getLogger(__name__)


class AllocationDetailView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    model = Allocation
    template_name = "allocation/allocation_detail.html"
    context_object_name = "allocation"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)

        if self.request.user.has_perm("allocation.can_view_all_allocations"):
            return True

        return allocation_obj.has_perm(self.request.user, AllocationPermission.USER)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        visit_allocation_detail.send(sender=self.__class__, allocation_pk=pk)
        allocation_obj = get_object_or_404(Allocation, pk=pk)
        allocation_users = allocation_obj.allocationuser_set.exclude(
            status__name__in=[
                "Removed",
            ]
        ).order_by("user__username")

        if ALLOCATION_EULA_ENABLE:
            user_in_allocation = allocation_users.filter(user=self.request.user).exists()
            context["user_in_allocation"] = user_in_allocation

            if user_in_allocation:
                allocation_user_status = get_object_or_404(
                    AllocationUser, allocation=allocation_obj, user=self.request.user
                ).status
                if allocation_obj.status.name == "Active" and allocation_user_status.name == "PendingEula":
                    messages.info(self.request, "This allocation is active, but you must agree to the EULA to use it!")

            context["eulas"] = allocation_obj.get_eula()
            context["res"] = allocation_obj.get_parent_resource.pk
            context["res_obj"] = allocation_obj.get_parent_resource

        # set visible usage attributes
        alloc_attr_set = allocation_obj.get_attribute_set(self.request.user, "view_allocationattribute")
        attributes_with_usage = [a for a in alloc_attr_set if hasattr(a, "allocationattributeusage")]
        attributes = alloc_attr_set

        allocation_changes = allocation_obj.allocationchangerequest_set.all().order_by("-pk")

        guage_data = []
        invalid_attributes = []
        for attribute in attributes_with_usage:
            try:
                guage_data.append(
                    generate_guauge_data_from_usage(
                        attribute.allocation_attribute_type.name,
                        float(attribute.value),
                        float(attribute.allocationattributeusage.value),
                    )
                )
            except ValueError:
                logger.error(
                    "Allocation attribute '%s' is not an int but has a usage", attribute.allocation_attribute_type.name
                )
                invalid_attributes.append(attribute)

        for a in invalid_attributes:
            attributes_with_usage.remove(a)

        context["allocation_users"] = allocation_users
        context["guage_data"] = guage_data
        context["attributes_with_usage"] = attributes_with_usage
        context["attributes"] = attributes
        context["allocation_changes"] = allocation_changes
        context["allocation_changes_enabled"] = allocation_obj.is_changeable

        # Can the user update the project?
        context["is_allowed_to_update_project"] = allocation_obj.project.has_perm(
            self.request.user, ProjectPermission.UPDATE, "change_project"
        )
        context["allocation_user_roles_enabled"] = check_if_roles_are_enabled(allocation_obj)

        noteset = allocation_obj.allocationusernote_set
        if self.request.user.is_superuser or self.request.user.has_perm("allocation.view_allocationusernote"):
            notes = noteset.all()
        else:
            notes = noteset.filter(is_private=False)

        context["user_has_permissions"] = check_if_groups_in_review_groups(
            allocation_obj.get_parent_resource.review_groups.all(), self.request.user.groups.all(), "change_allocation"
        )

        if self.request.user.is_superuser:
            context["user_has_permissions"] = True

        context["user_exists_in_allocation"] = allocation_obj.allocationuser_set.filter(
            user=self.request.user,
            status__name__in=["Active", "Invited", "Pending", "Disabled", "Retired"],
        ).exists()

        context["can_move_allocation"] = False
        if "coldfront.plugins.movable_allocations" in settings.INSTALLED_APPS:
            context["can_move_allocation"] = check_if_groups_in_review_groups(
                allocation_obj.get_parent_resource.review_groups.all(),
                self.request.user.groups.all(),
                "can_move_allocations",
            )

        context["project"] = allocation_obj.project
        context["notes"] = notes.order_by("-created")
        context["ALLOCATION_ENABLE_ALLOCATION_RENEWAL"] = ALLOCATION_ENABLE_ALLOCATION_RENEWAL
        context["ALLOCATION_DAYS_TO_REVIEW_BEFORE_EXPIRING"] = ALLOCATION_DAYS_TO_REVIEW_BEFORE_EXPIRING
        context["ALLOCATION_DAYS_TO_REVIEW_AFTER_EXPIRING"] = ALLOCATION_DAYS_TO_REVIEW_AFTER_EXPIRING
        context["ALLOCATION_REMOVAL_REQUESTS_ALLOWED"] = ALLOCATION_REMOVAL_REQUESTS_ALLOWED
        return context

    def get(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)

        initial_data = {
            "status": allocation_obj.status,
            "end_date": allocation_obj.end_date,
            "start_date": allocation_obj.start_date,
            "description": allocation_obj.description,
            "is_locked": allocation_obj.is_locked,
            "is_changeable": allocation_obj.is_changeable,
        }

        form = AllocationUpdateForm(initial=initial_data)
        user_has_permissions = check_if_groups_in_review_groups(
            allocation_obj.get_parent_resource.review_groups.all(), self.request.user.groups.all(), "change_allocation"
        )

        if not self.request.user.is_superuser and not user_has_permissions:
            form.fields["is_locked"].disabled = True
            form.fields["is_changeable"].disabled = True

        context = self.get_context_data()
        context["form"] = form
        context["allocation"] = allocation_obj
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)
        if not request.user.is_superuser:
            group_exists = check_if_groups_in_review_groups(
                allocation_obj.get_parent_resource.review_groups.all(),
                self.request.user.groups.all(),
                "change_allocation",
            )
            if not group_exists:
                messages.error(request, "You do not have permission to update this allocation")
                return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": pk}))

        initial_data = {
            "status": allocation_obj.status,
            "end_date": allocation_obj.end_date,
            "start_date": allocation_obj.start_date,
            "description": allocation_obj.description,
            "is_locked": allocation_obj.is_locked,
            "is_changeable": allocation_obj.is_changeable,
        }
        form = AllocationUpdateForm(request.POST, initial=initial_data)

        if not form.is_valid():
            context = self.get_context_data()
            context["form"] = form
            context["allocation"] = allocation_obj
            return render(request, self.template_name, context)

        action = request.POST.get("action")
        if action not in ["update", "approve", "auto-approve", "deny"]:
            return HttpResponseBadRequest("Invalid request")

        form_data = form.cleaned_data
        old_status = allocation_obj.status.name

        if action in ["update", "approve", "deny"]:
            create_admin_action(request.user, form_data, allocation_obj)
            allocation_obj.end_date = form_data.get("end_date")
            allocation_obj.start_date = form_data.get("start_date")
            allocation_obj.description = form_data.get("description")
            allocation_obj.is_locked = form_data.get("is_locked")
            allocation_obj.is_changeable = form_data.get("is_changeable")
            allocation_obj.status = form_data.get("status")

        if "approve" in action:
            active_status = AllocationStatusChoice.objects.get(name="Active")
            create_admin_action(request.user, {"status": active_status}, allocation_obj)
            allocation_obj.status = active_status
        elif action == "deny":
            deny_status = AllocationStatusChoice.objects.get(name="Denied")
            create_admin_action(request.user, {"status": deny_status}, allocation_obj)
            allocation_obj.status = deny_status

        if old_status != "Active" == allocation_obj.status.name:
            if allocation_obj.project.status.name != "Active":
                messages.error(
                    request, "Project must be approved first before you can update this allocation's status!"
                )
                return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": pk}))
            if not allocation_obj.start_date:
                allocation_obj.start_date = datetime.datetime.now()
            if "approve" in action or not allocation_obj.end_date:
                allocation_obj.end_date = allocation_obj.project.end_date

            allocation_obj.save()

            allocation_activate.send(sender=self.__class__, allocation_pk=allocation_obj.pk)
            allocation_users = allocation_obj.allocationuser_set.exclude(
                status__name__in=["Removed", "Error", "DeclinedEULA", "PendingEULA"]
            )
            for allocation_user in allocation_users:
                allocation_activate_user.send(sender=self.__class__, allocation_user_pk=allocation_user.pk)

            addtl_context = {"help_url": EMAIL_TICKET_SYSTEM_ADDRESS}
            email_template = (
                EMAIL_RESOURCE_EMAIL_TEMPLATES.get(allocation_obj.get_parent_resource.name, {}).get(
                    "allocation_activated", "email/allocation_activated.txt"
                ),
            )
            send_allocation_customer_email(
                request,
                allocation_obj,
                "Allocation Activated",
                email_template,
                domain_url=get_domain_url(self.request),
                addtl_context=addtl_context,
            )
            if action != "auto-approve":
                messages.success(request, "Allocation Activated!")
            logger.info(
                f"Admin {request.user.username} approved a {allocation_obj.get_parent_resource.name} "
                f"allocation (allocation pk={allocation_obj.pk})"
            )

        elif old_status != allocation_obj.status.name in ["Denied", "New", "Revoked", "Removed"]:
            allocation_obj.end_date = datetime.datetime.now() if allocation_obj.status.name != "New" else None
            allocation_obj.save()

            if allocation_obj.status.name in ["Denied", "Revoked", "Removed"]:
                allocation_disable.send(sender=self.__class__, allocation_pk=allocation_obj.pk)
                allocation_users = allocation_obj.allocationuser_set.exclude(status__name__in=["Removed", "Error"])
                for allocation_user in allocation_users:
                    allocation_remove_user.send(sender=self.__class__, allocation_user_pk=allocation_user.pk)
            if allocation_obj.status.name == "Denied":
                send_allocation_customer_email(
                    request,
                    allocation_obj,
                    "Allocation Denied",
                    "email/allocation_denied.txt",
                    domain_url=get_domain_url(self.request),
                )
                messages.success(request, "Allocation Denied!")
            elif allocation_obj.status.name == "Revoked":
                send_allocation_customer_email(
                    request,
                    allocation_obj,
                    "Allocation Revoked",
                    "email/allocation_revoked.txt",
                    domain_url=get_domain_url(self.request),
                )
                messages.success(request, "Allocation Revoked!")
            elif allocation_obj.status.name == "Removed":
                if "coldfront.plugins.allocation_removal_requests" in settings.INSTALLED_APPS:
                    from coldfront.plugins.allocation_removal_requests.signals import (
                        allocation_remove,
                    )

                    allocation_remove.send(sender=self.__class__, allocation_pk=allocation_obj.pk)
                send_allocation_customer_email(
                    request,
                    allocation_obj,
                    "Allocation Removed",
                    "allocation_removal_requests/allocation_removed.txt",
                    domain_url=get_domain_url(self.request),
                )
                messages.success(request, "Allocation Removed!")
            else:
                messages.success(request, "Allocation updated!")
            logger.info(
                f"Admin {request.user.username} changed the status of a "
                f"{allocation_obj.get_parent_resource.name} allocation to "
                f"{allocation_obj.status.name} (allocation pk={allocation_obj.pk})"
            )
        else:
            messages.success(request, "Allocation updated!")
            allocation_obj.save()

        if action == "auto-approve":
            messages.success(
                request,
                "Allocation to {} has been ACTIVATED for {} {} ({})".format(
                    allocation_obj.get_parent_resource,
                    allocation_obj.project.pi.first_name,
                    allocation_obj.project.pi.last_name,
                    allocation_obj.project.pi.username,
                ),
            )
            return HttpResponseRedirect(reverse("allocation-request-list"))

        return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": pk}))


class AllocationEULAView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    model = Allocation
    template_name = "allocation/allocation_review_eula.html"
    context_object_name = "allocation-eula"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)

        if self.request.user.has_perm("allocation.can_view_all_allocations"):
            return True

        return allocation_obj.has_perm(self.request.user, AllocationPermission.USER)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)
        allocation_users = allocation_obj.allocationuser_set.exclude(
            status__name__in=[
                "Removed",
            ]
        ).order_by("user__username")
        user_in_allocation = allocation_users.filter(user=self.request.user).exists()

        context["allocation"] = allocation_obj.pk
        context["eulas"] = allocation_obj.get_eula()
        context["res"] = allocation_obj.get_parent_resource.pk
        context["res_obj"] = allocation_obj.get_parent_resource

        if user_in_allocation and ALLOCATION_EULA_ENABLE:
            allocation_user_status = get_object_or_404(
                AllocationUser, allocation=allocation_obj, user=self.request.user
            ).status
            context["allocation_user_status"] = allocation_user_status.name
            context["last_updated"] = get_object_or_404(
                AllocationUser, allocation=allocation_obj, user=self.request.user
            ).modified

        return context

    def get(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        get_object_or_404(Allocation, pk=pk)
        context = self.get_context_data()
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)
        allocation_users = allocation_obj.allocationuser_set.exclude(
            status__name__in=["Removed", "DeclinedEULA"]
        ).order_by("user__username")
        user_in_allocation = allocation_users.filter(user=self.request.user).exists()
        if user_in_allocation:
            allocation_user_obj = get_object_or_404(AllocationUser, allocation=allocation_obj, user=self.request.user)
            action = request.POST.get("action")
            if action not in ["accepted_eula", "declined_eula"]:
                return HttpResponseBadRequest("Invalid request")
            if "accepted_eula" in action:
                allocation_user_obj.status = AllocationUserStatusChoice.objects.get(name="Active")
                messages.success(self.request, "EULA Accepted!")
                if EMAIL_ALLOCATION_EULA_CONFIRMATIONS:
                    project_user = allocation_user_obj.allocation.project.projectuser_set.get(
                        user=allocation_user_obj.user
                    )
                    if EMAIL_ALLOCATION_EULA_IGNORE_OPT_OUT or project_user.enable_notifications:
                        send_allocation_eula_customer_email(
                            allocation_user_obj,
                            "EULA accepted",
                            "email/allocation_eula_accepted.txt",
                            cc_managers=EMAIL_ALLOCATION_EULA_CONFIRMATIONS_CC_MANAGERS,
                            include_eula=EMAIL_ALLOCATION_EULA_INCLUDE_ACCEPTED_EULA,
                        )
                if allocation_obj.status == AllocationStatusChoice.objects.get(name="Active"):
                    allocation_activate_user.send(sender=self.__class__, allocation_user_pk=allocation_user_obj.pk)
            elif action == "declined_eula":
                allocation_user_obj.status = AllocationUserStatusChoice.objects.get(name="DeclinedEULA")
                messages.warning(
                    self.request,
                    "You did not agree to the EULA and were removed from the allocation. To access this allocation, your PI will have to re-add you.",
                )
                if EMAIL_ALLOCATION_EULA_CONFIRMATIONS:
                    project_user = allocation_user_obj.allocation.project.projectuser_set.get(
                        user=allocation_user_obj.user
                    )
                    if EMAIL_ALLOCATION_EULA_IGNORE_OPT_OUT or project_user.enable_notifications:
                        send_allocation_eula_customer_email(
                            allocation_user_obj,
                            "EULA declined",
                            "email/allocation_eula_declined.txt",
                            cc_managers=EMAIL_ALLOCATION_EULA_CONFIRMATIONS_CC_MANAGERS,
                        )
            allocation_user_obj.save()

        return HttpResponseRedirect(reverse("allocation-review-eula", kwargs={"pk": pk}))


class AllocationListView(LoginRequiredMixin, ListView):
    model = Allocation
    template_name = "allocation/allocation_list.html"
    context_object_name = "allocation_list"
    paginate_by = 25

    def get_queryset(self):
        order_by = self.request.GET.get("order_by")
        if order_by:
            direction = self.request.GET.get("direction")
            dir_dict = {"asc": "", "des": "-"}
            order_by = dir_dict[direction] + order_by
        else:
            order_by = "id"

        allocation_search_form = AllocationSearchForm(self.request.GET)

        if allocation_search_form.is_valid():
            data = allocation_search_form.cleaned_data

            if data.get("show_all_allocations") and self.request.user.is_superuser:
                allocations = (
                    Allocation.objects.prefetch_related(
                        "project",
                        "project__pi",
                        "status",
                    )
                    .all()
                    .order_by(order_by)
                )
            elif data.get("show_all_allocations") and self.request.user.has_perm("allocation.can_view_all_allocations"):
                allocations = (
                    Allocation.objects.prefetch_related(
                        "project",
                        "project__pi",
                        "status",
                    )
                    .filter(resources__review_groups__in=list(self.request.user.groups.all()))
                    .order_by(order_by)
                )
            else:
                allocations = (
                    Allocation.objects.prefetch_related(
                        "project",
                        "project__pi",
                        "status",
                    )
                    .filter(
                        Q(
                            project__status__name__in=[
                                "New",
                                "Active",
                            ]
                        )
                        & Q(project__projectuser__status__name="Active")
                        & Q(project__projectuser__user=self.request.user)
                        & (
                            Q(project__projectuser__role__name="Manager")
                            | Q(allocationuser__user=self.request.user)
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
                    )
                    .distinct()
                    .order_by(order_by)
                )

            # Project Title
            if data.get("project"):
                allocations = allocations.filter(project__title__icontains=data.get("project"))

            # username
            if data.get("username"):
                allocations = allocations.filter(
                    Q(project__pi__username__icontains=data.get("username"))
                    | Q(allocationuser__user__username__icontains=data.get("username"))
                    & Q(
                        allocationuser__status__name__in=[
                            "PendingEULA",
                            "Active",
                            "Invited",
                            "Pending",
                            "Disabled",
                            "Retired",
                        ]
                    )
                )

            # Resource Type
            if data.get("resource_type"):
                allocations = allocations.filter(resources__resource_type=data.get("resource_type"))

            # Resource Name
            if data.get("resource_name"):
                allocations = allocations.filter(resources__in=data.get("resource_name"))

            # Allocation Attribute Name
            if data.get("allocation_attribute_name") and data.get("allocation_attribute_value"):
                allocations = allocations.filter(
                    Q(allocationattribute__allocation_attribute_type=data.get("allocation_attribute_name"))
                    & Q(allocationattribute__value=data.get("allocation_attribute_value"))
                )

            # End Date
            if data.get("end_date"):
                allocations = allocations.filter(end_date__lt=data.get("end_date"), status__name="Active").order_by(
                    "end_date"
                )

            # Active from now until date
            if data.get("active_from_now_until_date"):
                allocations = allocations.filter(end_date__gte=date.today())
                allocations = allocations.filter(
                    end_date__lt=data.get("active_from_now_until_date"), status__name="Active"
                ).order_by("end_date")

            # Status
            if data.get("status"):
                allocations = allocations.filter(status__in=data.get("status"))

        else:
            allocations = (
                Allocation.objects.prefetch_related(
                    "project",
                    "project__pi",
                    "status",
                )
                .filter(
                    Q(allocationuser__user=self.request.user)
                    & Q(
                        allocationuser__status__name__in=[
                            "PendingEULA",
                            "Active",
                            "Invited",
                            "Pending",
                            "Disabled",
                            "Retired",
                        ]
                    )
                )
                .order_by(order_by)
            )

        return allocations.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        allocations_count = self.get_queryset().count()
        context["allocations_count"] = allocations_count

        allocation_search_form = AllocationSearchForm(self.request.GET)

        if allocation_search_form.is_valid():
            data = allocation_search_form.cleaned_data
            filter_parameters = ""
            for key, value in data.items():
                if value:
                    if isinstance(value, QuerySet):
                        filter_parameters += "".join([f"{key}={ele.pk}&" for ele in value])
                    elif hasattr(value, "pk"):
                        filter_parameters += f"{key}={value.pk}&"
                    else:
                        filter_parameters += f"{key}={value}&"
            context["allocation_search_form"] = allocation_search_form
        else:
            filter_parameters = None
            context["allocation_search_form"] = AllocationSearchForm()

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

        allocation_list = context.get("allocation_list")
        paginator = Paginator(allocation_list, self.paginate_by)

        page = self.request.GET.get("page")

        try:
            allocation_list = paginator.page(page)
        except PageNotAnInteger:
            allocation_list = paginator.page(1)
        except EmptyPage:
            allocation_list = paginator.page(paginator.num_pages)

        return context


class AllocationCreateView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    form_class = AllocationForm
    template_name = "allocation/allocation_create.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        project_obj = get_object_or_404(Project, pk=self.kwargs.get("project_pk"))
        if project_obj.has_perm(self.request.user, ProjectPermission.UPDATE):
            return True

        messages.error(self.request, "You do not have permission to create a new allocation.")
        return False

    def dispatch(self, request, *args, **kwargs):
        project_obj = get_object_or_404(Project, pk=self.kwargs.get("project_pk"))

        if project_obj.needs_review:
            messages.error(
                request, "You cannot request a new allocation because you have to review your project first."
            )
            return HttpResponseRedirect(reverse("project-detail", kwargs={"pk": project_obj.pk}))

        if project_obj.status.name in [
            "Archived",
            "Denied",
            "Review Pending",
            "Expired",
            "Renewal Denied",
        ]:
            messages.error(
                request,
                'You cannot request a new allocation for a project with status "{}".'.format(project_obj.status.name),
            )
            return HttpResponseRedirect(reverse("project-detail", kwargs={"pk": project_obj.pk}))

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project_obj = get_object_or_404(Project, pk=self.kwargs.get("project_pk"))
        context["project"] = project_obj

        user_resources = get_user_resources(self.request.user)
        resources_form_default_quantities = {}
        resources_form_label_texts = {}
        resources_with_eula = {}
        attr_names = ("quantity_default_value", "quantity_label", "eula")
        for resource in user_resources:
            for attr_name in attr_names:
                query = Q(resource_attribute_type__name=attr_name)
                if resource.resourceattribute_set.filter(query).exists():
                    value = resource.resourceattribute_set.get(query).value
                    if attr_name == "quantity_default_value":
                        resources_form_default_quantities[resource.id] = int(value)
                    if attr_name == "quantity_label":
                        resources_form_label_texts[resource.id] = value
                    if attr_name == "eula":
                        resources_with_eula[resource.id] = value

        context["resources_form_default_quantities"] = resources_form_default_quantities
        context["resources_form_label_texts"] = resources_form_label_texts
        context["resources_with_eula"] = resources_with_eula
        context["resources_with_accounts"] = list(
            Resource.objects.filter(name__in=list(ALLOCATION_ACCOUNT_MAPPING.keys())).values_list("id", flat=True)
        )

        return context

    def get_form(self, form_class=None):
        """Return an instance of the form to be used in this view."""
        if form_class is None:
            form_class = self.get_form_class()
        return form_class(self.request.user, self.kwargs.get("project_pk"), **self.get_form_kwargs())

    def form_valid(self, form):
        form_data = form.cleaned_data
        project_obj = get_object_or_404(Project, pk=self.kwargs.get("project_pk"))
        resource_obj = form_data.get("resource")
        justification = form_data.get("justification")
        quantity = form_data.get("quantity", 1)
        allocation_account = form_data.get("allocation_account", None)
        # A resource is selected that requires an account name selection but user has no account names
        if (
            ALLOCATION_ACCOUNT_ENABLED
            and resource_obj.name in ALLOCATION_ACCOUNT_MAPPING
            and AllocationAttributeType.objects.filter(name=ALLOCATION_ACCOUNT_MAPPING[resource_obj.name]).exists()
            and not allocation_account
        ):
            form.add_error(
                None,
                format_html(
                    'You need to create an account name. Create it by clicking the link under the "Allocation account" field.'
                ),
            )
            return self.form_invalid(form)

        allocation_limit_objs = resource_obj.resourceattribute_set.filter(
            resource_attribute_type__name="allocation_limit"
        ).first()
        if allocation_limit_objs:
            allocation_limit = int(allocation_limit_objs.value)
            allocation_count = project_obj.allocation_set.filter(
                resources=resource_obj,
                status__name__in=["Active", "New", "Renewal Requested", "Paid", "Payment Pending", "Payment Requested"],
            ).count()
            if allocation_count >= allocation_limit:
                form.add_error(None, format_html("Your project is at the allocation limit allowed for this resource."))
                return self.form_invalid(form)

        usernames = form_data.get("users")
        usernames.append(project_obj.pi.username)
        usernames = list(set(usernames))

        users = [get_user_model().objects.get(username=username) for username in usernames]
        if project_obj.pi not in users:
            users.append(project_obj.pi)

        if INVOICE_ENABLED and resource_obj.requires_payment:
            allocation_status_obj = AllocationStatusChoice.objects.get(name=INVOICE_DEFAULT_STATUS)
        else:
            allocation_status_obj = AllocationStatusChoice.objects.get(name="New")

        allocation_obj = Allocation.objects.create(
            project=project_obj, justification=justification, quantity=quantity, status=allocation_status_obj
        )

        if ALLOCATION_ENABLE_CHANGE_REQUESTS_BY_DEFAULT:
            allocation_obj.is_changeable = True
            allocation_obj.save()

        allocation_obj.resources.add(resource_obj)

        if ALLOCATION_ACCOUNT_ENABLED and allocation_account and resource_obj.name in ALLOCATION_ACCOUNT_MAPPING:
            allocation_attribute_type_obj = AllocationAttributeType.objects.get(
                name=ALLOCATION_ACCOUNT_MAPPING[resource_obj.name]
            )
            AllocationAttribute.objects.create(
                allocation_attribute_type=allocation_attribute_type_obj,
                allocation=allocation_obj,
                value=allocation_account,
            )

        for linked_resource in resource_obj.linked_resources.all():
            allocation_obj.resources.add(linked_resource)

        allocation_user_active_status = AllocationUserStatusChoice.objects.get(name="Active")
        if ALLOCATION_EULA_ENABLE:
            allocation_user_pending_status = AllocationUserStatusChoice.objects.get(name="PendingEULA")
        for user in users:
            if ALLOCATION_EULA_ENABLE and not (user == self.request.user):
                AllocationUser.objects.create(
                    allocation=allocation_obj, user=user, status=allocation_user_pending_status
                )
            else:
                AllocationUser.objects.create(
                    allocation=allocation_obj, user=user, status=allocation_user_active_status
                )

        send_allocation_admin_email(
            allocation_obj,
            "New Allocation Request",
            "email/new_allocation_request.txt",
            domain_url=get_domain_url(self.request),
        )
        allocation_new.send(sender=self.__class__, allocation_pk=allocation_obj.pk)
        return super().form_valid(form)

    def get_success_url(self):
        msg = "Allocation requested. It will be available once it is approved."
        messages.success(self.request, msg)
        return reverse("project-detail", kwargs={"pk": self.kwargs.get("project_pk")})


class AllocationAddUsersView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "allocation/allocation_add_users.html"
    model = Allocation
    context_object_name = "allocation"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))
        if allocation_obj.has_perm(self.request.user, AllocationPermission.MANAGER, "add_allocationuser"):
            return True

        messages.error(self.request, "You do not have permission to add users to the allocation.")
        return False

    def dispatch(self, request, *args, **kwargs):
        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))

        message = None
        if allocation_obj.is_locked and not self.request.user.is_superuser:
            message = "You cannot modify this allocation because it is locked! Contact support for details."
        elif allocation_obj.status.name not in [
            "Active",
            "New",
            "Renewal Requested",
            "Payment Pending",
            "Payment Requested",
            "Paid",
        ]:
            message = f"You cannot add users to an allocation with status {allocation_obj.status.name}."
        elif allocation_obj.get_parent_resource.name == "Geode-Project":
            message = "You cannot add users to a Geode-Project allocation."
        if message:
            messages.error(request, message)
            return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": allocation_obj.pk}))
        return super().dispatch(request, *args, **kwargs)

    def get_users_to_add(self, allocation_obj):
        active_users_in_project = list(
            allocation_obj.project.projectuser_set.filter(status__name="Active").values_list(
                "user__username", flat=True
            )
        )
        users_already_in_allocation = list(
            allocation_obj.allocationuser_set.exclude(status__name__in=["Removed"]).values_list(
                "user__username", flat=True
            )
        )

        missing_users = list(set(active_users_in_project) - set(users_already_in_allocation))
        missing_users = get_user_model().objects.filter(username__in=missing_users)

        users_to_add = []
        for user in missing_users:
            users_to_add.append(
                {
                    "username": user.username,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "email": user.email,
                    "role": None,
                }
            )

        return users_to_add

    def get_dict_of_users_to_add(self, formset):
        users = {}
        for form in formset:
            user_form_data = form.cleaned_data
            if user_form_data["selected"]:
                users[user_form_data.get("username")] = user_form_data.get("role")

        return users

    def get_total_users_in_allocation_if_added(self, allocation_obj, selected_users):
        total_users = len(
            list(
                allocation_obj.allocationuser_set.exclude(status__name__in=["Removed"]).values_list(
                    "user__username", flat=True
                )
            )
        )
        total_users += len(selected_users)

        return total_users

    def get_disable_select_list(self, allocation_obj, usernames):
        disable_select_list = [False] * len(usernames)
        results = allocation_obj.get_parent_resource.check_users_accounts(usernames)
        for i, result in enumerate(results.values()):
            if not result.get("exists"):
                disable_select_list[i] = True
        return disable_select_list

    def get(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)

        users_to_add = self.get_users_to_add(allocation_obj)
        context = {}

        results = {}
        if users_to_add:
            formset = formset_factory(
                AllocationAddUserForm, max_num=len(users_to_add), formset=AllocationAddUserFormset
            )
            results = allocation_obj.get_parent_resource.check_users_accounts(
                [user.get("username") for user in users_to_add]
            )
            formset = formset(
                initial=users_to_add,
                prefix="userform",
                form_kwargs={
                    "resource": allocation_obj.get_parent_resource,
                    "disable_selected": [not result.get("exists") for result in results.values()],
                },
            )
            context["formset"] = formset

        context["allocation_user_roles_enabled"] = check_if_roles_are_enabled(allocation_obj)
        context["allocation"] = allocation_obj

        account_results = {}
        for username, result in results.items():
            account_results[username] = result.get("reason")
        context["account_results"] = account_results

        user_resources = get_user_resources(self.request.user)
        resources_with_eula = {}
        for res in user_resources:
            if res in allocation_obj.get_resources_as_list:
                if res.get_attribute_list(name="eula"):
                    for attr_value in res.get_attribute_list(name="eula"):
                        resources_with_eula[res] = attr_value

        context["resources_with_eula"] = resources_with_eula
        string_accumulator = ""
        for res, value in resources_with_eula.items():
            string_accumulator += f"{res}: {value}\n"
        context["compiled_eula"] = str(string_accumulator)

        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)

        users_to_add = self.get_users_to_add(allocation_obj)
        allocation_user_limit = allocation_obj.get_parent_resource.get_attribute("user_limit")

        formset = formset_factory(AllocationAddUserForm, max_num=len(users_to_add))
        formset = formset(
            request.POST,
            initial=users_to_add,
            prefix="userform",
            form_kwargs={"resource": allocation_obj.get_parent_resource},
        )

        if formset.is_valid():
            allocation_user_active_status_choice = AllocationUserStatusChoice.objects.get(name="Active")
            if ALLOCATION_EULA_ENABLE:
                allocation_user_pending_status_choice = AllocationUserStatusChoice.objects.get(name="PendingEULA")
            selected_users = self.get_dict_of_users_to_add(formset)

            user_account_results = allocation_obj.get_parent_resource.check_users_accounts(
                [selected_user for selected_user in selected_users.keys()]
            )

            missing_accounts = []
            missing_resource_accounts = []
            for username, result in user_account_results.items():
                if not result.get("exists"):
                    if result.get("reason") == "no_account":
                        missing_accounts.append(username)
                    elif result.get("reason") == "no_resource_account":
                        missing_resource_accounts.append(username)
                    selected_users.pop(username)

            if missing_accounts:
                message = "The following user does not have an IU account and was not added:"
                if len(missing_accounts) > 1:
                    message = "The following users do not have IU accounts and were not added:"
                messages.warning(request, f"{message} {', '.join(missing_accounts)}")
                logger.info(
                    f"User(s) {', '.join(missing_accounts)} do not have IU accounts and "
                    f"were not added to a {allocation_obj.get_parent_resource.name} "
                    f"allocation (allocation pk={allocation_obj.pk})"
                )

            if missing_resource_accounts:
                message = "The following user does not have an account on this resource and was not added:"
                if len(missing_resource_accounts) > 1:
                    message = "The following users do not have an account on this resource and were not added:"
                accounts_url = "https://access.iu.edu/Accounts/Create"
                messages.warning(
                    request,
                    format_html(
                        f"{message} {', '.join(missing_resource_accounts)}. Please direct them "
                        f'to <a href="{accounts_url}">{accounts_url}</a> to create one.'
                    ),
                )

                logger.info(
                    f"User(s) {', '.join(missing_resource_accounts)} were missing accounts for a "
                    f"{allocation_obj.get_parent_resource.name} allocation (allocation pk={allocation_obj.pk})"
                )

            if allocation_user_limit:
                total_users = self.get_total_users_in_allocation_if_added(
                    allocation_obj, [selected_user for selected_user in selected_users.keys()]
                )
                if total_users > int(allocation_user_limit):
                    messages.warning(
                        request,
                        f"Only {allocation_user_limit} users are allowed on this resource. Users "
                        f"were not added. (Total users counted: {total_users})",
                    )
                    return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": pk}))

            selected_user_objs = []
            for username, role in selected_users.items():
                user_obj = get_user_model().objects.get(username=username)
                selected_user_objs.append(user_obj)

                if allocation_obj.allocationuser_set.filter(user=user_obj).exists():
                    allocation_user_obj = allocation_obj.allocationuser_set.get(user=user_obj)
                    if ALLOCATION_EULA_ENABLE and not user_obj.userprofile.is_pi and allocation_obj.get_eula():
                        allocation_user_obj.status = allocation_user_pending_status_choice
                        send_email_template(
                            f"Agree to EULA for {allocation_obj.get_parent_resource.__str__()}",
                            "email/allocation_agree_to_eula.txt",
                            {
                                "resource": allocation_obj.get_parent_resource,
                                "url": build_link(
                                    reverse("allocation-review-eula", kwargs={"pk": allocation_obj.pk}),
                                    domain_url=get_domain_url(self.request),
                                ),
                            },
                            self.request.user.email,
                            [user_obj],
                        )
                    else:
                        allocation_user_obj.status = allocation_user_active_status_choice
                    allocation_user_obj.role = role
                    allocation_user_obj.save()
                else:
                    if ALLOCATION_EULA_ENABLE and not user_obj.userprofile.is_pi and allocation_obj.get_eula():
                        allocation_user_obj = AllocationUser.objects.create(
                            allocation=allocation_obj, user=user_obj, status=allocation_user_pending_status_choice, role=role
                        )
                        send_email_template(
                            f"Agree to EULA for {allocation_obj.get_parent_resource.__str__()}",
                            "email/allocation_agree_to_eula.txt",
                            {
                                "resource": allocation_obj.get_parent_resource,
                                "url": build_link(
                                    reverse("allocation-review-eula", kwargs={"pk": allocation_obj.pk}),
                                    domain_url=get_domain_url(self.request),
                                ),
                            },
                            self.request.user.email,
                            [user_obj],
                        )
                    else:
                        allocation_user_obj = AllocationUser.objects.create(
                            allocation=allocation_obj, user=user_obj, status=allocation_user_active_status_choice, role=role
                        )

                allocation_activate_user.send(sender=self.__class__, allocation_user_pk=allocation_user_obj.pk)

            if selected_users:
                allocation_added_users_emails = list(
                    allocation_obj.project.projectuser_set.filter(
                        user__in=selected_user_objs, enable_notifications=True
                    ).values_list("user__email", flat=True)
                )
                if allocation_obj.project.pi.email not in allocation_added_users_emails:
                    allocation_added_users_emails.append(allocation_obj.project.pi.email)

                send_added_user_email(request, allocation_obj, selected_user_objs, allocation_added_users_emails)

                is_plural = len(selected_users.keys()) > 1
                messages.success(
                    request,
                    f"User{'s' if is_plural else ''} added to the allocation: {', '.join(selected_users.keys())}",
                )

                logger.info(
                    f"User {request.user.username} added {', '.join(selected_users.keys())} "
                    f"to a {allocation_obj.get_parent_resource.name} allocation "
                    f"(allocation pk={allocation_obj.pk})"
                )
        else:
            for error in formset.errors:
                if error.get("__all__"):
                    messages.error(request, error.get("__all__")[0])
                    logger.warning(
                        f"An error occured when adding users to an allocation (allocation pk={allocation_obj.pk}). "
                        f"Error: {error.get('__all__')[0]}"
                    )
                    return HttpResponseRedirect(reverse("allocation-add-users", kwargs={"pk": pk}))

        return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": pk}))


class AllocationRemoveUsersView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "allocation/allocation_remove_users.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))
        if allocation_obj.has_perm(self.request.user, AllocationPermission.MANAGER, "delete_allocationuser"):
            return True

        messages.error(self.request, "You do not have permission to remove users from allocation.")
        return False

    def dispatch(self, request, *args, **kwargs):
        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))

        message = None
        if allocation_obj.is_locked and not self.request.user.is_superuser:
            message = "You cannot modify this allocation because it is locked! Contact support for details."
        elif allocation_obj.status.name not in [
            "Active",
            "New",
            "Renewal Requested",
        ]:
            message = f"You cannot remove users from a allocation with status {allocation_obj.status.name}."
        elif allocation_obj.get_parent_resource.name == "Geode-Project":
            message = "You cannot remove users from a Geode-Project allocation."
        if message:
            messages.error(request, message)
            return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": allocation_obj.pk}))
        return super().dispatch(request, *args, **kwargs)

    def get_users_to_remove(self, allocation_obj):
        users_to_remove = list(
            allocation_obj.allocationuser_set.exclude(
                status__name__in=[
                    "Removed",
                    "Error",
                ]
            ).values_list("user__username", flat=True)
        )

        users_to_remove = (
            get_user_model()
            .objects.filter(username__in=users_to_remove)
            .exclude(pk__in=[allocation_obj.project.pi.pk, self.request.user.pk])
        )
        users_to_remove = [
            {
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": user.email,
            }
            for user in users_to_remove
        ]

        return users_to_remove

    def get(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)

        users_to_remove = self.get_users_to_remove(allocation_obj)
        context = {}

        if users_to_remove:
            formset = formset_factory(AllocationRemoveUserForm, max_num=len(users_to_remove))
            formset = formset(initial=users_to_remove, prefix="userform")
            context["formset"] = formset

        context["allocation"] = allocation_obj
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)

        users_to_remove = self.get_users_to_remove(allocation_obj)

        formset = formset_factory(AllocationRemoveUserForm, max_num=len(users_to_remove))
        formset = formset(request.POST, initial=users_to_remove, prefix="userform")

        remove_users_count = 0

        if formset.is_valid():
            allocation_user_removed_status_choice = AllocationUserStatusChoice.objects.get(name="Removed")
            allocation_user_status_choice = allocation_user_removed_status_choice

            removed_user_objs = []
            for form in formset:
                user_form_data = form.cleaned_data
                if user_form_data["selected"]:
                    remove_users_count += 1

                    user_obj = get_user_model().objects.get(username=user_form_data.get("username"))
                    if allocation_obj.project.pi == user_obj:
                        continue

                    allocation_user_obj = allocation_obj.allocationuser_set.get(user=user_obj)
                    removed_user_objs.append(user_obj)

                    allocation_user_obj.status = allocation_user_status_choice
                    allocation_user_obj.save()
                    allocation_remove_user.send(sender=self.__class__, allocation_user_pk=allocation_user_obj.pk)

            if removed_user_objs:
                removed_users = [removed_user_obj.username for removed_user_obj in removed_user_objs]
                allocation_removed_users_emails = list(
                    allocation_obj.project.projectuser_set.filter(
                        user__in=removed_user_objs, enable_notifications=True
                    ).values_list("user__email", flat=True)
                )
                if allocation_obj.project.pi.email not in allocation_removed_users_emails:
                    allocation_removed_users_emails.append(allocation_obj.project.pi.email)

                send_removed_user_email(request, allocation_obj, removed_user_objs, allocation_removed_users_emails)
                user_plural = "user" if remove_users_count == 1 else "users"
                messages.success(request, f"Removed {user_plural} {', '.join(removed_users)} from allocation.")

                logger.info(
                    f"User {request.user.username} removed {', '.join(removed_users)} from a "
                    f"{allocation_obj.get_parent_resource.name} allocation (allocation pk={allocation_obj.pk})"
                )
        else:
            for error in formset.errors:
                messages.error(request, error)

        return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": pk}))


class AllocationAttributeCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = AllocationAttribute
    form_class = AllocationAttributeCreateForm
    template_name = "allocation/allocation_allocationattribute_create.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))
        user = self.request.user
        if user.is_superuser:
            return True

        group_exists = check_if_groups_in_review_groups(
            allocation_obj.get_parent_resource.review_groups.all(),
            self.request.user.groups.all(),
            "add_allocationattribute",
        )
        if group_exists:
            return True

        messages.error(self.request, "You do not have permission to add allocation attributes.")
        return False

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)
        context["allocation"] = allocation_obj
        return context

    def get_initial(self):
        initial = super().get_initial()
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)
        initial["allocation"] = allocation_obj
        return initial

    def get_form(self, form_class=None):
        """Return an instance of the form to be used in this view."""
        form = super().get_form(form_class)
        form.fields["allocation"].widget = forms.HiddenInput()

        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))
        current_allocation_attribute_objs = allocation_obj.allocationattribute_set.all()
        current_allocation_attribute_type_objs = []
        for allocation_attribute_obj in current_allocation_attribute_objs:
            current_allocation_attribute_type_objs.append(allocation_attribute_obj.allocation_attribute_type)
        allocation_attribute_type_objs = AllocationAttributeType.objects.all()
        allocation_attribute_type_pks = []
        for allocation_attribute_type_obj in allocation_attribute_type_objs:
            if allocation_attribute_type_obj in current_allocation_attribute_type_objs:
                continue

            if allocation_obj.get_parent_resource in allocation_attribute_type_obj.get_linked_resources():
                allocation_attribute_type_pks.append(allocation_attribute_type_obj.pk)
        form.fields["allocation_attribute_type"].queryset = AllocationAttributeType.objects.filter(
            pk__in=allocation_attribute_type_pks
        )

        return form

    def get_success_url(self):
        allocation_obj = Allocation.objects.get(pk=self.kwargs.get("pk"))
        logger.info(
            f"Admin {self.request.user.username} created a {allocation_obj.get_parent_resource.name} "
            f"allocation attribute (allocation pk={allocation_obj.pk})"
        )
        create_admin_action_for_creation(self.request.user, self.object, allocation_obj)
        return reverse("allocation-detail", kwargs={"pk": allocation_obj.pk})


class AllocationAttributeDeleteView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "allocation/allocation_allocationattribute_delete.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))
        user = self.request.user
        if user.is_superuser:
            return True

        group_exists = check_if_groups_in_review_groups(
            allocation_obj.get_parent_resource.review_groups.all(),
            self.request.user.groups.all(),
            "delete_allocationattribute",
        )
        if group_exists:
            return True

        messages.error(self.request, "You do not have permission to delete attributes from this allocation.")
        return False

    def get_allocation_attributes_to_delete(self, allocation_obj):
        allocation_attributes_to_delete = AllocationAttribute.objects.filter(allocation=allocation_obj)
        allocation_attributes_to_delete = [
            {
                "pk": attribute.pk,
                "name": attribute.allocation_attribute_type.name,
                "value": attribute.value,
            }
            for attribute in allocation_attributes_to_delete
        ]

        return allocation_attributes_to_delete

    def get(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)

        allocation_attributes_to_delete = self.get_allocation_attributes_to_delete(allocation_obj)
        context = {}

        if allocation_attributes_to_delete:
            formset = formset_factory(AllocationAttributeDeleteForm, max_num=len(allocation_attributes_to_delete))
            formset = formset(initial=allocation_attributes_to_delete, prefix="attributeform")
            context["formset"] = formset
        context["allocation"] = allocation_obj
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)

        allocation_attributes_to_delete = self.get_allocation_attributes_to_delete(allocation_obj)

        formset = formset_factory(AllocationAttributeDeleteForm, max_num=len(allocation_attributes_to_delete))
        formset = formset(request.POST, initial=allocation_attributes_to_delete, prefix="attributeform")

        attributes_deleted_count = 0

        if formset.is_valid():
            for form in formset:
                form_data = form.cleaned_data
                if form_data["selected"]:
                    attributes_deleted_count += 1

                    allocation_attribute = AllocationAttribute.objects.get(pk=form_data["pk"])

                    logger.info(
                        f"Admin {request.user.username} deleted a {allocation_obj.get_parent_resource.name} "
                        f"allocation attribute (allocation pk={allocation_obj.pk})"
                    )
                    create_admin_action_for_deletion(
                        request.user, allocation_attribute, allocation_attribute.allocation
                    )

                    allocation_attribute.delete()

            messages.success(request, f"Deleted {attributes_deleted_count} attributes from allocation.")
        else:
            for error in formset.errors:
                messages.error(request, error)

        return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": pk}))


class AllocationNoteCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = AllocationUserNote
    fields = "__all__"
    template_name = "allocation/allocation_note_create.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))
        user = self.request.user
        if user.is_superuser:
            return True

        group_exists = check_if_groups_in_review_groups(
            allocation_obj.get_parent_resource.review_groups.all(),
            self.request.user.groups.all(),
            "add_allocationusernote",
        )
        if group_exists:
            return True

        messages.error(self.request, "You do not have permission to add a note to this allocation.")
        return False

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)
        context["allocation"] = allocation_obj
        return context

    def get_initial(self):
        initial = super().get_initial()
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)
        author = self.request.user
        initial["allocation"] = allocation_obj
        initial["author"] = author
        return initial

    def get_form(self, form_class=None):
        """Return an instance of the form to be used in this view."""
        form = super().get_form(form_class)
        form.fields["allocation"].widget = forms.HiddenInput()
        form.fields["author"].widget = forms.HiddenInput()
        form.order_fields(["allocation", "author", "note", "is_private"])
        return form

    def get_success_url(self):
        logger.info(
            f"Admin {self.request.user.username} created an allocation note (allocation pk={self.object.allocation.pk})"
        )
        return reverse("allocation-detail", kwargs={"pk": self.object.allocation.pk})


class AllocationRequestListView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "allocation/allocation_request_list.html"
    login_url = "/"

    def test_func(self):
        """UserPassesTestMixin Tests"""

        if self.request.user.is_superuser:
            return True

        if self.request.user.has_perm("allocation.can_review_allocation_requests"):
            return True

        messages.error(self.request, "You do not have permission to review allocation requests.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_superuser:
            allocation_list = Allocation.objects.filter(
                status__name__in=[
                    "New",
                    "Paid",
                    "Billing Information Submitted",
                    "Contacted By Admin",
                    "Waiting For Admin Approval",
                ]
            ).exclude(project__status__name__in=["Archived", "Renewal Denied"])
            allocation_renewal_list = Allocation.objects.filter(status__name="Renewal Requested").exclude(
                project__status__name__in=["Archived", "Renewal Denied"]
            )
        else:
            allocation_list = (
                Allocation.objects.filter(
                    status__name__in=[
                        "New",
                        "Paid",
                        "Billing Information Submitted",
                        "Contacted By Admin",
                        "Waiting For Admin Approval",
                    ],
                    resources__review_groups__in=list(self.request.user.groups.all()),
                )
                .exclude(project__status__name__in=["Archived", "Renewal Denied"])
                .distinct()
            )
            allocation_renewal_list = (
                Allocation.objects.filter(
                    status__name="Renewal Requested", resources__review_groups__in=list(self.request.user.groups.all())
                )
                .exclude(project__status__name__in=["Archived", "Renewal Denied"])
                .distinct()
            )

        context["allocation_status_active"] = AllocationStatusChoice.objects.get(name="Active")
        context["allocation_list"] = allocation_list
        context["allocation_renewal_list"] = allocation_renewal_list
        context["PROJECT_ENABLE_PROJECT_REVIEW"] = PROJECT_ENABLE_PROJECT_REVIEW
        context["ALLOCATION_DEFAULT_ALLOCATION_LENGTH"] = ALLOCATION_DEFAULT_ALLOCATION_LENGTH
        return context


class AllocationRenewView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "allocation/allocation_renew.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))
        if allocation_obj.has_perm(self.request.user, AllocationPermission.MANAGER, "can_review_allocation_requests"):
            return True

        messages.error(self.request, "You do not have permission to renew allocation.")
        return False

    def dispatch(self, request, *args, **kwargs):
        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))

        if not allocation_obj.project.requires_review:
            messages.error(request, "Your allocation does not need to be renewed.")
            return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": allocation_obj.pk}))

        if not ALLOCATION_ENABLE_ALLOCATION_RENEWAL:
            messages.error(
                request,
                "Allocation renewal is disabled. Request a new allocation to this resource if you want to continue using it after the active until date.",
            )
            return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": allocation_obj.pk}))

        if allocation_obj.status.name not in [
            "Active",
            "Expired",
            "Revoked",
        ]:
            messages.error(request, f"You cannot renew a allocation with status {allocation_obj.status.name}.")
            return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": allocation_obj.pk}))

        if allocation_obj.project.status.name in [
            "Denied",
            "Expired",
            "Archived",
            "Renewal Denied",
        ]:
            messages.error(
                request,
                'You cannot renew an allocation with project status "{}".'.format(allocation_obj.project.status.name),
            )
            return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": allocation_obj.pk}))

        if not allocation_obj.project.get_env.get("renewable"):
            messages.error(request, f"You cannot renew allocations in a {allocation_obj.project.type.name} project.")
            return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": allocation_obj.pk}))

        if allocation_obj.project.needs_review:
            messages.error(request, "You cannot renew your allocation until you review your project first.")
            return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": allocation_obj.pk}))

        if allocation_obj.expires_in > ALLOCATION_DAYS_TO_REVIEW_BEFORE_EXPIRING:
            messages.error(request, "It is too soon to renew your allocation.")
            return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": allocation_obj.pk}))

        if (
            ALLOCATION_DAYS_TO_REVIEW_AFTER_EXPIRING > 0
            and allocation_obj.expires_in < -ALLOCATION_DAYS_TO_REVIEW_AFTER_EXPIRING
        ):
            messages.error(request, "It is too late to renew your allocation.")
            return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": allocation_obj.pk}))

        if allocation_obj.is_locked:
            messages.error(request, "You cannot renew this allocation.")
            return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": allocation_obj.pk}))

        return super().dispatch(request, *args, **kwargs)

    def get_users_in_allocation(self, allocation_obj):
        users_in_allocation = (
            allocation_obj.allocationuser_set.exclude(status__name__in=["Removed"])
            .exclude(user__pk__in=[allocation_obj.project.pi.pk, self.request.user.pk])
            .order_by("user__username")
        )

        users = [
            {
                "username": allocation_user.user.username,
                "first_name": allocation_user.user.first_name,
                "last_name": allocation_user.user.last_name,
                "email": allocation_user.user.email,
            }
            for allocation_user in users_in_allocation
        ]

        return users

    def get(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)

        users_in_allocation = self.get_users_in_allocation(allocation_obj)
        context = {}

        if users_in_allocation:
            formset = formset_factory(AllocationReviewUserForm, max_num=len(users_in_allocation))
            formset = formset(initial=users_in_allocation, prefix="userform")
            context["formset"] = formset

            context["resource_eula"] = {}
            if allocation_obj.get_parent_resource.resourceattribute_set.filter(
                resource_attribute_type__name="eula"
            ).exists():
                value = allocation_obj.get_parent_resource.resourceattribute_set.get(
                    resource_attribute_type__name="eula"
                ).value
                context["resource_eula"].update({"eula": value})

        context["allocation"] = allocation_obj
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)

        users_in_allocation = self.get_users_in_allocation(allocation_obj)

        formset = formset_factory(AllocationReviewUserForm, max_num=len(users_in_allocation))
        formset = formset(request.POST, initial=users_in_allocation, prefix="userform")

        allocation_renewal_requested_status_choice = AllocationStatusChoice.objects.get(name="Renewal Requested")
        allocation_user_removed_status_choice = AllocationUserStatusChoice.objects.get(name="Removed")
        project_user_remove_status_choice = ProjectUserStatusChoice.objects.get(name="Removed")

        allocation_obj.status = allocation_renewal_requested_status_choice
        allocation_obj.save()

        if not users_in_allocation or formset.is_valid():
            if users_in_allocation:
                for form in formset:
                    user_form_data = form.cleaned_data
                    user_obj = get_user_model().objects.get(username=user_form_data.get("username"))
                    user_status = user_form_data.get("user_status")

                    if user_status == "keep_in_project_only":
                        allocation_user_obj = allocation_obj.allocationuser_set.get(user=user_obj)
                        allocation_user_obj.status = allocation_user_removed_status_choice
                        allocation_user_obj.save()

                        allocation_remove_user.send(sender=self.__class__, allocation_user_pk=allocation_user_obj.pk)

                    elif user_status == "remove_from_project":
                        for active_allocation in allocation_obj.project.allocation_set.filter(
                            status__name__in=(
                                "Active",
                                "Denied",
                                "New",
                                "Paid",
                                "Payment Pending",
                                "Payment Requested",
                                "Payment Declined",
                                "Renewal Requested",
                                "Unpaid",
                            )
                        ):
                            allocation_user_obj = active_allocation.allocationuser_set.get(user=user_obj)

                            if not allocation_user_obj.exists():
                                continue
                            allocation_user_obj = allocation_user_obj[0]
                            allocation_user_obj.status = allocation_user_removed_status_choice
                            allocation_user_obj.save()
                            allocation_remove_user.send(
                                sender=self.__class__, allocation_user_pk=allocation_user_obj.pk
                            )

                        project_user_obj = ProjectUser.objects.get(project=allocation_obj.project, user=user_obj)
                        project_user_obj.status = project_user_remove_status_choice
                        project_user_obj.save()

            project_obj = allocation_obj.project
            addtl_context = {
                "project_title": project_obj.title,
                "project_id": project_obj.pk,
            }
            send_allocation_admin_email(
                allocation_obj,
                "Allocation Renewal Requested",
                "email/allocation_renewed.txt",
                domain_url=get_domain_url(self.request),
                addtl_context=addtl_context,
            )

            logger.info(
                f"User {request.user.username} sent a {allocation_obj.get_parent_resource.name} "
                f"allocation renewal request (allocation pk={allocation_obj.pk})"
            )
            messages.success(request, "Allocation renewal submitted")
        else:
            if not formset.is_valid():
                for error in formset.errors:
                    messages.error(request, error)
        return HttpResponseRedirect(reverse("project-detail", kwargs={"pk": allocation_obj.project.pk}))


class AllocationInvoiceListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = Allocation
    template_name = "allocation/allocation_invoice_list.html"
    context_object_name = "allocation_list"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True

        if self.request.user.has_perm("allocation.can_manage_invoice"):
            return True

        messages.error(self.request, "You do not have permission to manage invoices.")
        return False

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_superuser:
            resource_objs = Resource.objects.filter(requires_payment=True)
        else:
            resource_objs = Resource.objects.filter(
                review_groups__in=list(self.request.user.groups.all()), requires_payment=True
            )
        resources = []
        for resource in resource_objs:
            resources.append((resource.name, resource.name))

        return context

    def get_queryset(self):
        if self.request.user.is_superuser:
            allocations = Allocation.objects.filter(
                status__name__in=[
                    "Active",
                ]
            )
        else:
            allocations = Allocation.objects.filter(
                status__name__in=[
                    "Active",
                ],
                resources__review_groups__in=list(self.request.user.groups.all()),
            )
        allocations_require_payment = []
        for allocation in allocations:
            if allocation.get_parent_resource.requires_payment:
                allocations_require_payment.append(allocation)

        return allocations_require_payment


# this is the view class thats rendering allocation_invoice_detail.
# each view class has a view template that renders
class AllocationInvoiceDetailView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    model = Allocation
    template_name = "allocation/allocation_invoice_detail.html"
    context_object_name = "allocation"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True

        if self.request.user.has_perm("allocation.can_manage_invoice"):
            return True

        messages.error(self.request, "You do not have permission to view invoices.")
        return False

    def get_context_data(self, **kwargs):
        """Create all the variables for allocation_invoice_detail.html"""
        context = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)
        allocation_users = allocation_obj.allocationuser_set.exclude(status__name__in=["Removed"]).order_by(
            "user__username"
        )

        alloc_attr_set = allocation_obj.get_attribute_set(self.request.user)

        attributes_with_usage = [a for a in alloc_attr_set if hasattr(a, "allocationattributeusage")]
        attributes = [a for a in alloc_attr_set]

        guage_data = []
        invalid_attributes = []
        for attribute in attributes_with_usage:
            try:
                guage_data.append(
                    generate_guauge_data_from_usage(
                        attribute.allocation_attribute_type.name,
                        float(attribute.value),
                        float(attribute.allocationattributeusage.value),
                    )
                )
            except ValueError:
                logger.error(
                    "Allocation attribute '%s' is not an int but has a usage", attribute.allocation_attribute_type.name
                )
                invalid_attributes.append(attribute)

        for a in invalid_attributes:
            attributes_with_usage.remove(a)

        context["guage_data"] = guage_data
        context["attributes_with_usage"] = attributes_with_usage
        context["attributes"] = attributes

        # Can the user update the project?
        context["is_allowed_to_update_project"] = allocation_obj.project.has_perm(
            self.request.user, ProjectPermission.UPDATE
        )
        context["allocation_users"] = allocation_users

        if self.request.user.is_superuser:
            notes = allocation_obj.allocationusernote_set.all()
        else:
            notes = allocation_obj.allocationusernote_set.filter(is_private=False)

        context["notes"] = notes
        context["ALLOCATION_ENABLE_ALLOCATION_RENEWAL"] = ALLOCATION_ENABLE_ALLOCATION_RENEWAL
        return context

    def get(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)

        initial_data = {
            "status": allocation_obj.status,
        }

        form = AllocationInvoiceUpdateForm(initial=initial_data)

        context = self.get_context_data()
        context["form"] = form
        context["allocation"] = allocation_obj

        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)

        initial_data = {
            "status": allocation_obj.status,
        }
        form = AllocationInvoiceUpdateForm(request.POST, initial=initial_data)

        if form.is_valid():
            form_data = form.cleaned_data
            allocation_obj.status = form_data.get("status")
            allocation_obj.save()
            messages.success(request, "Allocation updated!")
        else:
            for error in form.errors:
                messages.error(request, error)
        return HttpResponseRedirect(reverse("allocation-invoice-detail", kwargs={"pk": pk}))


class AllocationAddInvoiceNoteView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = AllocationUserNote
    template_name = "allocation/allocation_add_invoice_note.html"
    fields = (
        "is_private",
        "note",
    )

    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True

        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))
        group_exists = check_if_groups_in_review_groups(
            allocation_obj.get_parent_resource.review_groups.all(), self.request.user.groups.all(), "can_manage_invoice"
        )
        if group_exists:
            return True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)
        context["allocation"] = allocation_obj
        return context

    def form_valid(self, form):
        # This method is called when valid form data has been POSTed.
        # It should return an HttpResponse.
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)
        obj = form.save(commit=False)
        obj.author = self.request.user
        obj.allocation = allocation_obj
        obj.save()
        allocation_obj.save()
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("allocation-invoice-detail", kwargs={"pk": self.object.allocation.pk})


class AllocationUpdateInvoiceNoteView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = AllocationUserNote
    template_name = "allocation/allocation_update_invoice_note.html"
    fields = (
        "is_private",
        "note",
    )

    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True

        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))
        group_exists = check_if_groups_in_review_groups(
            allocation_obj.get_parent_resource.review_groups.all(), self.request.user.groups.all(), "can_manage_invoice"
        )
        if group_exists:
            return True

        messages.error(self.request, "You do not have permission to manage invoices.")
        return False

    def get_success_url(self):
        return reverse_lazy("allocation-invoice-detail", kwargs={"pk": self.object.allocation.pk})


class AllocationDeleteInvoiceNoteView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "allocation/allocation_delete_invoice_note.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True

        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))
        group_exists = check_if_groups_in_review_groups(
            allocation_obj.get_parent_resource.review_groups.all(), self.request.user.groups.all(), "can_manage_invoice"
        )
        if group_exists:
            return True

        messages.error(self.request, "You do not have permission to manage invoices.")
        return False

    def get_notes_to_delete(self, allocation_obj):
        notes_to_delete = [
            {
                "pk": note.pk,
                "note": note.note,
                "author": note.author.username,
            }
            for note in allocation_obj.allocationusernote_set.all()
        ]

        return notes_to_delete

    def get(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)
        notes_to_delete = self.get_notes_to_delete(allocation_obj)
        context = {}
        if notes_to_delete:
            formset = formset_factory(AllocationInvoiceNoteDeleteForm, max_num=len(notes_to_delete))
            formset = formset(initial=notes_to_delete, prefix="noteform")
            context["formset"] = formset
        context["allocation"] = allocation_obj
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)
        notes_to_delete = self.get_notes_to_delete(allocation_obj)

        formset = formset_factory(AllocationInvoiceNoteDeleteForm, max_num=len(notes_to_delete))
        formset = formset(request.POST, initial=notes_to_delete, prefix="noteform")

        if formset.is_valid():
            for form in formset:
                note_form_data = form.cleaned_data
                if note_form_data["selected"]:
                    note_obj = AllocationUserNote.objects.get(pk=note_form_data.get("pk"))
                    note_obj.delete()
        else:
            for error in formset.errors:
                messages.error(request, error)

        return HttpResponseRedirect(reverse_lazy("allocation-invoice-detail", kwargs={"pk": allocation_obj.pk}))


class AllocationAccountCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = AllocationAccount
    template_name = "allocation/allocation_allocationaccount_create.html"
    form_class = AllocationAccountForm

    def test_func(self):
        """UserPassesTestMixin Tests"""

        if not ALLOCATION_ACCOUNT_ENABLED:
            return False
        if self.request.user.is_superuser:
            return True
        if self.request.user.userprofile.is_pi:
            return True

        messages.error(self.request, "You do not have permission to add allocation attributes.")
        return False

    def form_invalid(self, form):
        response = super().form_invalid(form)
        if self.request.is_ajax():
            return JsonResponse(form.errors, status=400)
        return response

    def form_valid(self, form):
        form.instance.user = self.request.user
        response = super().form_valid(form)
        if self.request.is_ajax():
            data = {
                "pk": self.object.pk,
            }
            return JsonResponse(data)
        return response

    def get_success_url(self):
        return reverse_lazy("allocation-account-list")


class AllocationAccountListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = AllocationAccount
    template_name = "allocation/allocation_account_list.html"
    context_object_name = "allocationaccount_list"

    def test_func(self):
        """UserPassesTestMixin Tests"""

        if not ALLOCATION_ACCOUNT_ENABLED:
            return False
        if self.request.user.is_superuser:
            return True
        if self.request.user.userprofile.is_pi:
            return True

        messages.error(self.request, "You do not have permission to manage invoices.")
        return False

    def get_queryset(self):
        return AllocationAccount.objects.filter(user=self.request.user)


class AllocationChangeDetailView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    formset_class = AllocationAttributeUpdateForm
    template_name = "allocation/allocation_change_detail.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        allocation_change_obj = get_object_or_404(AllocationChangeRequest, pk=self.kwargs.get("pk"))

        if self.request.user.has_perm("allocation.can_view_all_allocations"):
            return True

        if allocation_change_obj.allocation.has_perm(
            self.request.user, AllocationPermission.MANAGER, "view_allocationchangerequest"
        ):
            return True

        return False

    def get_allocation_attributes_to_change(self, allocation_change_obj):
        attributes_to_change = allocation_change_obj.allocationattributechangerequest_set.all()

        attributes_to_change = [
            {
                "change_pk": attribute_change.pk,
                "attribute_pk": attribute_change.allocation_attribute.pk,
                "name": attribute_change.allocation_attribute.allocation_attribute_type.name,
                "value": attribute_change.allocation_attribute.value,
                "new_value": attribute_change.new_value,
                "old_value": attribute_change.old_value,
            }
            for attribute_change in attributes_to_change
        ]

        return attributes_to_change

    def get_context_data(self, **kwargs):
        context = {}

        allocation_change_obj = get_object_or_404(AllocationChangeRequest, pk=self.kwargs.get("pk"))

        allocation_attributes_to_change = self.get_allocation_attributes_to_change(allocation_change_obj)

        allocation_obj = allocation_change_obj.allocation
        if allocation_attributes_to_change:
            user_can_change = check_if_groups_in_review_groups(
                allocation_obj.get_parent_resource.review_groups.all(),
                self.request.user.groups.all(),
                "change_allocationattributechangerequest",
            )
            formset = formset_factory(self.formset_class, max_num=len(allocation_attributes_to_change))
            formset = formset(
                initial=allocation_attributes_to_change,
                prefix="attributeform",
                form_kwargs={"new_value_disabled": not user_can_change},
            )
            context["formset"] = formset

        context["user_has_permissions"] = check_if_groups_in_review_groups(
            allocation_obj.get_parent_resource.review_groups.all(),
            self.request.user.groups.all(),
            "view_allocationchangerequest",
        )

        if self.request.user.is_superuser:
            context["user_has_permissions"] = True

        context["allocation_change"] = allocation_change_obj
        context["attribute_changes"] = allocation_attributes_to_change
        context["user_can_delete"] = check_if_groups_in_review_groups(
            allocation_obj.get_parent_resource.review_groups.all(),
            self.request.user.groups.all(),
            "delete_allocationattributechangerequest",
        )
        context["identifiers"] = allocation_obj.allocationattribute_set.filter(
            allocation_attribute_type__name__in=ALLOCATION_ATTRIBUTE_IDENTIFIERS
        ).values_list("value", flat=True)

        return context

    def get(self, request, *args, **kwargs):
        allocation_change_obj = get_object_or_404(AllocationChangeRequest, pk=self.kwargs.get("pk"))

        allocation_change_form = AllocationChangeForm(
            initial={
                "justification": allocation_change_obj.justification,
                "end_date_extension": allocation_change_obj.end_date_extension,
            }
        )
        allocation_change_form.fields["justification"].disabled = True
        if allocation_change_obj.status.name != "Pending":
            allocation_change_form.fields["end_date_extension"].disabled = True
        if (
            not not self.request.user.has_perm("allocation.can_view_all_allocations")
            and not self.request.user.is_superuser
        ):
            allocation_change_form.fields["end_date_extension"].disabled = True

        note_form = AllocationChangeNoteForm(initial={"notes": allocation_change_obj.notes})

        context = self.get_context_data()

        context["allocation_change_form"] = allocation_change_form
        context["note_form"] = note_form
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        pk = self.kwargs.get("pk")
        allocation_change_obj = get_object_or_404(AllocationChangeRequest, pk=pk)
        allocation_obj = allocation_change_obj.allocation
        if not request.user.is_superuser:
            group_exists = check_if_groups_in_review_groups(
                allocation_obj.get_parent_resource.review_groups.all(),
                self.request.user.groups.all(),
                "change_allocationchangerequest",
            )
            if not group_exists:
                messages.error(
                    request, "You do not have permission to manage this allocation change request with this resource."
                )
                return HttpResponseRedirect(
                    reverse("allocation-change-detail", kwargs={"pk": allocation_change_obj.pk})
                )

        allocation_change_form = AllocationChangeForm(
            request.POST,
            initial={
                "justification": allocation_change_obj.justification,
                "end_date_extension": allocation_change_obj.end_date_extension,
            },
        )
        allocation_change_form.fields["justification"].required = False

        allocation_attributes_to_change = self.get_allocation_attributes_to_change(allocation_change_obj)

        if allocation_attributes_to_change:
            user_can_change = check_if_groups_in_review_groups(
                allocation_obj.get_parent_resource.review_groups.all(),
                self.request.user.groups.all(),
                "change_allocationattributechangerequest",
            )
            formset = formset_factory(self.formset_class, max_num=len(allocation_attributes_to_change))
            formset = formset(
                request.POST,
                initial=allocation_attributes_to_change,
                prefix="attributeform",
                form_kwargs={"new_value_disabled": not user_can_change},
            )

        note_form = AllocationChangeNoteForm(request.POST, initial={"notes": allocation_change_obj.notes})

        if not note_form.is_valid():
            allocation_change_form = AllocationChangeForm(
                initial={"justification": allocation_change_obj.justification}
            )
            allocation_change_form.fields["justification"].disabled = True

            context = self.get_context_data()

            context["note_form"] = note_form
            context["allocation_change_form"] = allocation_change_form
            return render(request, self.template_name, context)

        notes = note_form.cleaned_data.get("notes")

        action = request.POST.get("action")
        if action not in ["update", "approve", "deny"]:
            return HttpResponseBadRequest("Invalid request")

        if action == "deny":
            create_admin_action(request.user, {"notes": notes}, allocation_obj, allocation_change_obj)
            allocation_change_obj.notes = notes

            allocation_change_status_denied_obj = AllocationChangeStatusChoice.objects.get(name="Denied")
            allocation_change_obj.status = allocation_change_status_denied_obj

            allocation_change_obj.save()

            messages.success(
                request,
                "Allocation change request to {} has been DENIED for {} {} ({})".format(
                    allocation_change_obj.allocation.resources.first(),
                    allocation_change_obj.allocation.project.pi.first_name,
                    allocation_change_obj.allocation.project.pi.last_name,
                    allocation_change_obj.allocation.project.pi.username,
                ),
            )

            send_allocation_customer_email(
                request,
                allocation_change_obj.allocation,
                "Allocation Change Denied",
                "email/allocation_change_denied.txt",
                domain_url=get_domain_url(self.request),
            )

            logger.info(
                f"Admin {request.user.username} denied a {allocation_obj.get_parent_resource.name} "
                f"allocation change request (allocation pk={allocation_obj.pk})"
            )
            return HttpResponseRedirect(reverse("allocation-change-detail", kwargs={"pk": pk}))

        if not allocation_change_form.is_valid() or (allocation_attributes_to_change and not formset.is_valid()):
            for error in allocation_change_form.errors:
                messages.error(request, error)
            if allocation_attributes_to_change:
                attribute_errors = ""
                for error in formset.errors:
                    if error:
                        attribute_errors += error.get("__all__")
                messages.error(request, attribute_errors)
            return HttpResponseRedirect(reverse("allocation-change-detail", kwargs={"pk": pk}))

        allocation_change_obj.notes = notes

        if action == "update" and allocation_change_obj.status.name != "Pending":
            allocation_change_obj.save()
            messages.success(request, "Allocation change request updated!")
            logger.info(
                f"Admin {request.user.username} updated a {allocation_obj.get_parent_resource.name} "
                f"allocation change request (allocation pk={allocation_obj.pk})"
            )
            return HttpResponseRedirect(reverse("allocation-change-detail", kwargs={"pk": pk}))

        form_data = allocation_change_form.cleaned_data
        end_date_extension = form_data.get("end_date_extension")

        if not allocation_attributes_to_change and end_date_extension == 0:
            messages.error(request, "You must make a change to the allocation.")
            return HttpResponseRedirect(reverse("allocation-change-detail", kwargs={"pk": pk}))

        if end_date_extension != allocation_change_obj.end_date_extension:
            create_admin_action(request.user, {"end_date": end_date_extension}, allocation_obj)
            allocation_change_obj.end_date_extension = end_date_extension

        if allocation_attributes_to_change:
            for entry in formset:
                formset_data = entry.cleaned_data
                new_value = formset_data.get("new_value")
                attribute_change = AllocationAttributeChangeRequest.objects.get(pk=formset_data.get("change_pk"))

                if new_value != attribute_change.new_value:
                    create_admin_action(request.user, {"new_value": new_value}, allocation_obj, attribute_change)
                    attribute_change.new_value = new_value
                    attribute_change.save()

        if action == "update":
            allocation_change_obj.save()
            messages.success(request, "Allocation change request updated!")
            logger.info(
                f"Admin {request.user.username} updated a {allocation_obj.get_parent_resource.name} "
                f"allocation change request (allocation pk={allocation_obj.pk})"
            )

        elif action == "approve":
            allocation_obj = allocation_change_obj.allocation
            allocation_change_status_active_obj = AllocationChangeStatusChoice.objects.get(name="Approved")
            allocation_change_obj.status = allocation_change_status_active_obj

            if allocation_change_obj.end_date_extension > 0:
                create_admin_action(
                    request.user,
                    {"end_date_extension": form_data.get("end_date_extension")},
                    allocation_obj,
                    allocation_change_obj,
                )
                new_end_date = allocation_change_obj.allocation.end_date + relativedelta(
                    days=allocation_change_obj.end_date_extension
                )
                allocation_change_obj.allocation.end_date = new_end_date

                allocation_change_obj.allocation.save()

            allocation_change_obj.save()
            if allocation_attributes_to_change:
                attribute_change_list = allocation_change_obj.allocationattributechangerequest_set.all()
                for attribute_change in attribute_change_list:
                    create_admin_action(
                        request.user, {"new_value": attribute_change.new_value}, allocation_obj, attribute_change
                    )
                    attribute_change.allocation_attribute.value = attribute_change.new_value
                    attribute_change.allocation_attribute.save()
                    allocation_attribute_changed.send(
                        sender=self.__class__,
                        attribute_pk=attribute_change.allocation_attribute.pk,
                        allocation_pk=attribute_change.allocation.pk,
                    )

            messages.success(
                request,
                "Allocation change request to {} has been APPROVED for {} {} ({})".format(
                    allocation_change_obj.allocation.get_parent_resource,
                    allocation_change_obj.allocation.project.pi.first_name,
                    allocation_change_obj.allocation.project.pi.last_name,
                    allocation_change_obj.allocation.project.pi.username,
                ),
            )

            allocation_change_approved.send(
                sender=self.__class__,
                allocation_pk=allocation_change_obj.allocation.pk,
                allocation_change_pk=allocation_change_obj.pk,
            )

            send_allocation_customer_email(
                allocation_change_obj.allocation,
                "Allocation Change Approved",
                "email/allocation_change_approved.txt",
                domain_url=get_domain_url(self.request),
            )

            logger.info(
                f"Admin {request.user.username} approved a {allocation_obj.get_parent_resource.name} "
                f"allocation change request (allocation pk={allocation_obj.pk})"
            )

        return HttpResponseRedirect(reverse("allocation-change-detail", kwargs={"pk": pk}))


class AllocationChangeListView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "allocation/allocation_change_list.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""

        if self.request.user.is_superuser:
            return True

        if self.request.user.has_perm("allocation.view_allocationchangerequest"):
            return True

        messages.error(self.request, "You do not have permission to review allocation requests.")

        return False

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_superuser:
            allocation_change_list = AllocationChangeRequest.objects.filter(
                status__name__in=[
                    "Pending",
                ]
            )
        else:
            allocation_change_list = AllocationChangeRequest.objects.filter(
                status__name__in=[
                    "Pending",
                ],
                allocation__resources__review_groups__in=list(self.request.user.groups.all()),
            )
        context["allocation_change_list"] = allocation_change_list
        context["PROJECT_ENABLE_PROJECT_REVIEW"] = PROJECT_ENABLE_PROJECT_REVIEW
        return context


class AllocationChangeView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    formset_class = AllocationAttributeChangeForm
    template_name = "allocation/allocation_change.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))
        if allocation_obj.has_perm(self.request.user, AllocationPermission.MANAGER, "add_allocationchangerequest"):
            return True

        messages.error(self.request, "You do not have permission to request changes to this allocation.")
        return False

    def dispatch(self, request, *args, **kwargs):
        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))

        if allocation_obj.project.needs_review:
            messages.error(
                request, "You cannot request a change to this allocation because you have to review your project first."
            )
            return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": allocation_obj.pk}))

        if allocation_obj.project.status.name in [
            "Denied",
            "Expired",
            "Revoked",
        ]:
            messages.error(
                request,
                'You cannot request a change to an allocation in a project with status "{}".'.format(
                    allocation_obj.project.status.name
                ),
            )
            return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": allocation_obj.pk}))

        if allocation_obj.is_locked:
            messages.error(request, "You cannot request a change to a locked allocation.")
            return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": allocation_obj.pk}))

        if allocation_obj.status.name not in [
            "Active",
            "Renewal Requested",
            "Payment Pending",
            "Payment Requested",
            "Paid",
        ]:
            messages.error(
                request, f'You cannot request a change to an allocation with status "{allocation_obj.status.name}".'
            )
            return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": allocation_obj.pk}))

        if allocation_obj.allocationchangerequest_set.filter(status__name="Pending"):
            messages.error(request, "You cannot request a change to an allocation with a pending change request")
            return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": allocation_obj.pk}))

        return super().dispatch(request, *args, **kwargs)

    def get_allocation_attributes_to_change(self, allocation_obj):
        attributes_to_change = allocation_obj.allocationattribute_set.filter(
            allocation_attribute_type__is_changeable=True
        )

        attributes_to_change = [
            {
                "pk": attribute.pk,
                "name": attribute.allocation_attribute_type.name,
                "value": attribute.value,
                "old_value": attribute.value,
            }
            for attribute in attributes_to_change
        ]

        return attributes_to_change

    def get(self, request, *args, **kwargs):
        context = {}

        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))

        form = AllocationChangeForm(**self.get_form_kwargs())
        context["form"] = form

        allocation_attributes_to_change = self.get_allocation_attributes_to_change(allocation_obj)

        if allocation_attributes_to_change:
            formset = formset_factory(self.formset_class, max_num=len(allocation_attributes_to_change))
            formset = formset(initial=allocation_attributes_to_change, prefix="attributeform")
            context["formset"] = formset
        context["allocation"] = allocation_obj
        context["attributes"] = allocation_attributes_to_change
        if allocation_obj.get_parent_resource.name == "Slate Project":
            context["identifier"] = allocation_obj.allocationattribute_set.get(
                allocation_attribute_type__name="Slate Project Directory"
            ).value
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        change_requested = False
        attribute_changes_to_make = set({})

        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)

        form = AllocationChangeForm(**self.get_form_kwargs())

        allocation_attributes_to_change = self.get_allocation_attributes_to_change(allocation_obj)

        if allocation_attributes_to_change:
            formset = formset_factory(self.formset_class, max_num=len(allocation_attributes_to_change))
            formset = formset(request.POST, initial=allocation_attributes_to_change, prefix="attributeform")

            if form.is_valid() and formset.is_valid():
                form_data = form.cleaned_data

                if form_data.get("end_date_extension") != 0:
                    change_requested = True

                for entry in formset:
                    formset_data = entry.cleaned_data

                    new_value = formset_data.get("new_value")

                    if new_value != "":
                        change_requested = True

                        allocation_attribute = AllocationAttribute.objects.get(pk=formset_data.get("pk"))
                        attribute_changes_to_make.add((allocation_attribute, new_value))

                if change_requested:
                    end_date_extension = form_data.get("end_date_extension")
                    justification = form_data.get("justification")

                    change_request_status_obj = AllocationChangeStatusChoice.objects.get(name="Pending")

                    allocation_change_request_obj = AllocationChangeRequest.objects.create(
                        allocation=allocation_obj,
                        end_date_extension=end_date_extension,
                        justification=justification,
                        status=change_request_status_obj,
                    )

                    for attribute in attribute_changes_to_make:
                        attribute_change_request_obj = AllocationAttributeChangeRequest.objects.create(
                            allocation_change_request=allocation_change_request_obj,
                            allocation_attribute=attribute[0],
                            old_value=attribute[0].value,
                            new_value=attribute[1],
                        )
                    messages.success(request, "Allocation change request successfully submitted.")

                    logger.info(
                        f"User {request.user.username} requested a {allocation_obj.get_parent_resource.name} "
                        f"allocation change (allocation pk={allocation_obj.pk})"
                    )

                    # TODO - review this
                    pi_name = "{} {} ({})".format(
                        allocation_obj.project.pi.first_name,
                        allocation_obj.project.pi.last_name,
                        allocation_obj.project.pi.username,
                    )
                    resource_name = allocation_obj.get_parent_resource

                    project_obj = allocation_obj.project

                    addtl_context = {
                        "project_title": project_obj.title,
                        "project_id": project_obj.pk,
                    }
                    allocation_change_created.send(
                        sender=self.__class__,
                        allocation_pk=allocation_obj.pk,
                        allocation_change_pk=allocation_change_request_obj.pk,
                    )
                    send_allocation_admin_email(
                        allocation_obj,
                        f"New Allocation Change Request: {pi_name} - {resource_name}",
                        "email/new_allocation_change_request.txt",
                        url_path=reverse("allocation-change-list"),
                        domain_url=get_domain_url(self.request),
                        addtl_context=addtl_context,
                    )
                    return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": pk}))

                else:
                    messages.error(request, "You must request a change.")
                    return HttpResponseRedirect(reverse("allocation-change", kwargs={"pk": pk}))

            else:
                attribute_errors = []
                for error in form.errors:
                    messages.error(request, error)
                for error in formset.errors:
                    if error.get("__all__") is not None:
                        attribute_errors.append(error.get("__all__")[0])

                if attribute_errors:
                    messages.error(request, ", ".join(attribute_errors))
                return HttpResponseRedirect(reverse("allocation-change", kwargs={"pk": pk}))
        else:
            if form.is_valid():
                form_data = form.cleaned_data

                if form_data.get("end_date_extension") != 0:
                    end_date_extension = form_data.get("end_date_extension")
                    justification = form_data.get("justification")

                    change_request_status_obj = AllocationChangeStatusChoice.objects.get(name="Pending")

                    allocation_change_request_obj = AllocationChangeRequest.objects.create(
                        allocation=allocation_obj,
                        end_date_extension=end_date_extension,
                        justification=justification,
                        status=change_request_status_obj,
                    )
                    messages.success(request, "Allocation change request successfully submitted.")

                    pi_name = "{} {} ({})".format(
                        allocation_obj.project.pi.first_name,
                        allocation_obj.project.pi.last_name,
                        allocation_obj.project.pi.username,
                    )
                    resource_name = allocation_obj.get_parent_resource

                    addtl_context = {
                        "project_title": project_obj.title,
                        "project_id": project_obj.pk,
                    }
                    send_allocation_admin_email(
                        allocation_obj,
                        f"New Allocation Change Request: {pi_name} - {resource_name}",
                        "email/new_allocation_change_request.txt",
                        url_path=reverse("allocation-change-list"),
                        domain_url=get_domain_url(self.request),
                        addtl_context=addtl_context,
                    )

                    return HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": pk}))
                else:
                    messages.error(request, "You must request a change.")
                    return HttpResponseRedirect(reverse("allocation-change", kwargs={"pk": pk}))

            else:
                for error in form.errors:
                    messages.error(request, error)
                return HttpResponseRedirect(reverse("allocation-change", kwargs={"pk": pk}))


class AllocationAttributeEditView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    formset_class = AllocationAttributeEditForm
    template_name = "allocation/allocation_attribute_edit.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))
        user = self.request.user
        if user.is_superuser:
            return True

        group_exists = check_if_groups_in_review_groups(
            allocation_obj.get_parent_resource.review_groups.all(), user.groups.all(), "change_allocationattribute"
        )
        if group_exists:
            return True

        messages.error(self.request, "You do not have permission to edit this allocation's attributes.")

        return False

    def get_allocation_attributes_to_change(self, allocation_obj):
        attributes_to_change = allocation_obj.allocationattribute_set.all()

        attributes_to_change = [
            {
                "attribute_pk": attribute.pk,
                "name": attribute.allocation_attribute_type.name,
                "orig_value": attribute.value,
                "value": attribute.value,
            }
            for attribute in attributes_to_change
        ]

        return attributes_to_change

    def get(self, request, *args, **kwargs):
        context = {}
        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))
        allocation_attributes_to_change = self.get_allocation_attributes_to_change(allocation_obj)
        context["allocation"] = allocation_obj

        if not allocation_attributes_to_change:
            return render(request, self.template_name, context)

        AllocAttrChangeFormsetFactory = formset_factory(
            self.formset_class,
            max_num=len(allocation_attributes_to_change),
        )
        formset = AllocAttrChangeFormsetFactory(
            initial=allocation_attributes_to_change,
            prefix="attributeform",
        )
        context["formset"] = formset
        context["attributes"] = allocation_attributes_to_change
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        attribute_changes_to_make = set()

        pk = self.kwargs.get("pk")
        allocation_obj = get_object_or_404(Allocation, pk=pk)
        allocation_attributes_to_change = self.get_allocation_attributes_to_change(allocation_obj)

        ok_redirect = HttpResponseRedirect(reverse("allocation-detail", kwargs={"pk": pk}))
        if not allocation_attributes_to_change:
            return ok_redirect

        AllocAttrChangeFormsetFactory = formset_factory(
            self.formset_class,
            max_num=len(allocation_attributes_to_change),
        )
        formset = AllocAttrChangeFormsetFactory(
            request.POST,
            initial=allocation_attributes_to_change,
            prefix="attributeform",
        )
        if not formset.is_valid():
            attribute_errors = ""
            for error in formset.errors:
                if error:
                    attribute_errors += error.get("__all__")
            messages.error(request, attribute_errors)
            error_redirect = HttpResponseRedirect(reverse("allocation-attribute-edit", kwargs={"pk": pk}))
            return error_redirect

        for entry in formset:
            formset_data = entry.cleaned_data
            value = formset_data.get("value")

            if value != "":
                allocation_attribute = AllocationAttribute.objects.get(pk=formset_data.get("attribute_pk"))
                if allocation_attribute.value != value:
                    attribute_changes_to_make.add((allocation_attribute, value))

        for allocation_attribute, value in attribute_changes_to_make:
            create_admin_action(request.user, {"value": value}, allocation_obj, allocation_attribute)
            allocation_attribute.value = value
            allocation_attribute.save()
            allocation_attribute_changed.send(
                sender=self.__class__,
                attribute_pk=allocation_attribute.pk,
                allocation_pk=pk,
            )
            logger.info(
                f"Admin {request.user.username} updated a {allocation_obj.get_parent_resource.name} "
                f"allocation attribute (allocation pk={allocation_obj.pk})"
            )

        if attribute_changes_to_make:
            messages.success(request, "Successfully updated allocation attributes.")

        return ok_redirect


class AllocationChangeDeleteAttributeView(LoginRequiredMixin, UserPassesTestMixin, View):
    login_url = "/"

    def test_func(self):
        """UserPassesTestMixin Tests"""

        if self.request.user.is_superuser:
            return True

        allocation_attribute_change_obj = get_object_or_404(AllocationAttributeChangeRequest, pk=self.kwargs.get("pk"))
        allocation_obj = allocation_attribute_change_obj.allocation_change_request.allocation
        group_exists = check_if_groups_in_review_groups(
            allocation_obj.get_parent_resource.review_groups.all(),
            self.request.user.groups.all(),
            "delete_allocationattributechangerequest",
        )
        if group_exists:
            return True

        messages.error(self.request, "You do not have permission to delete an allocation attribute change request.")
        return False

    def get(self, request, pk):
        allocation_attribute_change_obj = get_object_or_404(AllocationAttributeChangeRequest, pk=pk)
        allocation_change_pk = allocation_attribute_change_obj.allocation_change_request.pk

        create_admin_action_for_deletion(
            request.user,
            allocation_attribute_change_obj,
            allocation_attribute_change_obj.allocation_change_request.allocation,
            allocation_attribute_change_obj.allocation_change_request,
        )

        allocation_attribute_change_obj.delete()

        allocation_pk = allocation_attribute_change_obj.allocation_change_request.allocation.pk
        allocation_resource_name = (
            allocation_attribute_change_obj.allocation_change_request.allocation.get_parent_resource.name
        )
        logger.info(
            f"Admin {request.user.username} deleted a {allocation_resource_name} allocation "
            f"attribute change request (allocation pk={allocation_pk})"
        )
        messages.success(request, "Allocation attribute change request successfully deleted.")
        return HttpResponseRedirect(reverse("allocation-change-detail", kwargs={"pk": allocation_change_pk}))


class AllocationUserDetailView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "allocation/allocation_user_detail.html"

    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True

        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))

        if allocation_obj.project.pi == self.request.user:
            return True

        if allocation_obj.project.projectuser_set.filter(
            user=self.request.user, role__name="Manager", status__name="Active"
        ).exists():
            return True

    def get(self, request, *args, **kwargs):
        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))
        allocation_user_pk = self.kwargs.get("allocation_user_pk")

        if allocation_obj.allocationuser_set.filter(pk=allocation_user_pk).exists():
            allocation_user_obj = allocation_obj.allocationuser_set.get(pk=allocation_user_pk)

            allocation_user_update_form = AllocationUserUpdateForm(
                resource=allocation_obj.get_parent_resource,
                initial={
                    "role": allocation_user_obj.role,
                },
            )

            context = {}
            context["can_update"] = not allocation_obj.project.pi == allocation_user_obj.user
            context["allocation_obj"] = allocation_obj
            context["allocation_user_update_form"] = allocation_user_update_form
            context["allocation_user_obj"] = allocation_user_obj
            context["allocation_user_roles_enabled"] = check_if_roles_are_enabled(allocation_obj)

            return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("pk"))
        project_obj = allocation_obj.project
        allocation_user_pk = self.kwargs.get("allocation_user_pk")

        if allocation_obj.status.name not in ["Active", "Billing Information Submitted", "New", "Renewal Requested"]:
            messages.error(request, f"You cannot update a user in a(n) {allocation_obj.status.name} allocation.")
            return HttpResponseRedirect(
                reverse(
                    "allocation-user-detail", kwargs={"pk": allocation_obj.pk, "allocation_user_pk": allocation_user_pk}
                )
            )

        if allocation_obj.allocationuser_set.filter(id=allocation_user_pk).exists():
            allocation_user_obj = allocation_obj.allocationuser_set.get(pk=allocation_user_pk)

            if allocation_user_obj.user == allocation_user_obj.allocation.project.pi:
                messages.error(request, "PI role cannot be changed.")
                return HttpResponseRedirect(
                    reverse(
                        "allocation-user-detail",
                        kwargs={"pk": allocation_obj.pk, "allocation_user_pk": allocation_user_pk},
                    )
                )

            allocation_user_update_form = AllocationUserUpdateForm(
                request.POST,
                resource=allocation_obj.get_parent_resource,
                initial={
                    "role": allocation_user_obj.role,
                },
            )

            if allocation_user_update_form.is_valid():
                form_data = allocation_user_update_form.cleaned_data
                if allocation_user_obj.role == form_data.get("role"):
                    return HttpResponseRedirect(
                        reverse(
                            "allocation-user-detail",
                            kwargs={"pk": allocation_obj.pk, "allocation_user_pk": allocation_user_pk},
                        )
                    )
                allocation_user_obj.role = form_data.get("role")
                allocation_user_obj.save()
                allocation_change_user_role.send(
                    sender=self.__class__,
                    allocation_user_pk=allocation_user_pk,
                )

                logger.info(
                    f"User {request.user.username} updated {allocation_user_obj.user.username}'s "
                    f"role (allocation pk={project_obj.pk})"
                )

                messages.success(request, "User details updated.")
                return HttpResponseRedirect(
                    reverse(
                        "allocation-user-detail",
                        kwargs={"pk": allocation_obj.pk, "allocation_user_pk": allocation_user_pk},
                    )
                )
            else:
                messages.error(request, allocation_user_update_form.errors.get("__all__"))
                return HttpResponseRedirect(
                    reverse(
                        "allocation-user-detail",
                        kwargs={"pk": allocation_obj.pk, "allocation_user_pk": allocation_user_pk},
                    )
                )


class AllocationNoteUpdateView(SuccessMessageMixin, LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = AllocationUserNote
    template_name = "allocation/allocation_note_update.html"
    fields = ["is_private", "note"]
    success_message = "Allocation note updated."

    def test_func(self):
        """UserPassesTestMixin Tests"""
        allocation_note_obj = get_object_or_404(AllocationUserNote, pk=self.kwargs.get("pk"))
        allocation_obj = get_object_or_404(Allocation, pk=self.kwargs.get("allocation_pk"))
        user = self.request.user
        if user.is_superuser:
            return True

        group_exists = check_if_groups_in_review_groups(
            allocation_obj.get_parent_resource.review_groups.all(),
            self.request.user.groups.all(),
            "change_allocationusernote",
        )
        if not group_exists:
            messages.error(self.request, "You do not have permission to update notes in this allocation.")
            return False

        if user != allocation_note_obj.author:
            messages.error(self.request, "Only the original author can edit this note.")
            return False

        return True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        allocation_pk = self.kwargs.get("allocation_pk")
        allocation_obj = get_object_or_404(Allocation, pk=allocation_pk)
        context["allocation"] = allocation_obj
        return context

    def get_success_url(self):
        logger.info(
            f"Admin {self.request.user.username} updated an allocation note (allocation pk={self.object.allocation.pk})"
        )
        return reverse("allocation-detail", kwargs={"pk": self.object.allocation.pk})
