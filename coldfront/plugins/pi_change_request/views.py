from functools import cached_property

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import transaction
from django.forms.formsets import formset_factory
from django.shortcuts import HttpResponse, get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import CreateView, TemplateView, View

from coldfront.core.project.models import Project
from coldfront.core.resource.models import Resource
from coldfront.core.utils.common import get_domain_url
from coldfront.core.utils.groups import check_if_groups_in_review_groups
from coldfront.plugins.pi_change_request.forms import (
    ProjectPiChangeRequestForm,
    ResourcesRequiringApprovalForm,
    ResourcesRequiringApprovalFormset,
)
from coldfront.plugins.pi_change_request.models import (
    ProjectPiChangeRequest,
    ProjectPiChangeRequestResourceApproval,
    ProjectPiChangeRequestResourceApprovalSetting,
    ProjectPiChangeRequestResourceApprovalStatusChoice,
    ProjectPiChangeRequestStatusChoice,
    ProjectPiChangeRequestUserApproval,
    ProjectPiChangeRequestUserApprovalStatusChoice,
)
from coldfront.plugins.pi_change_request.utils import send_email, send_ready_email, send_slack_message

RESOURCE_APPROVAL_SETTING_PERMISSION = "pi_change_request.change_projectpichangerequestresourceapprovalsetting"
RESOURCE_APPROVAL_PERMISSION = "pi_change_request.change_projectpichangerequestresourceapproval"


class ProjectPiChangeRequestView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = ProjectPiChangeRequest
    template_name_suffix = "_form"
    form_class = ProjectPiChangeRequestForm
    success_message = "Project PI change request received."

    @cached_property
    def project(self):
        return get_object_or_404(Project, pk=self.kwargs.get("pk"))

    def test_func(self):
        if self.request.user.is_superuser:
            return True

        project_obj = self.project
        if self.request.user == project_obj.pi:
            return True

        if project_obj.projectuser_set.filter(
            user=self.request.user, role__name="Manager", status__name="Active"
        ).exists():
            return True

    def get_form_kwargs(self, *args, **kwargs):
        kwargs = super().get_form_kwargs(*args, **kwargs)
        kwargs["project"] = self.project

        return kwargs

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        # Assign the project to the form here so it exists when form_valid is called. Prevents an
        # error in the clean method.
        form.instance.project = self.project
        return form

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        context["project_pk"] = self.kwargs.get("pk")
        return context

    def form_valid(self, form):
        request_obj = form.instance
        request_obj.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("New")
        request_obj.current_pi = request_obj.project.pi
        request_obj.initiator = self.request.user

        with transaction.atomic():
            response = super().form_valid(form)
            request_obj.resources.set(
                request_obj.project.allocation_set.filter(status__name="Active").values_list("resources", flat=True)
            )
            request_obj.create_resource_approvals()
            request_obj.create_user_approvals([request_obj.current_pi, request_obj.new_pi])

        domain_url = get_domain_url(self.request)
        project_review_url = reverse("pi-change-request-center")
        url = "{}{}".format(domain_url, project_review_url)
        send_slack_message(self.project, url)

        template_context = {
            "url": url,
            "project_title": self.project.title,
            "project_id": self.project.pk,
            "initiator": request_obj.initiator,
            "current_pi": request_obj.current_pi,
            "new_pi": request_obj.new_pi,
            "help_email": settings.EMAIL_TICKET_SYSTEM_ADDRESS,
        }
        send_email(
            "New Project PI Change Request",
            "pi_change_request/email/new_pi_change_request.txt",
            template_context,
            settings.EMAIL_TICKET_SYSTEM_ADDRESS,
        )

        return response

    def get_success_url(self):
        return self.object.project.get_absolute_url()


class ProjectPiChangeRequestCenterView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "pi_change_request/pi_change_request_center.html"

    def test_func(self):
        return True

    @cached_property
    def managed_resource_ids(self):
        """Return ids of resources this user may manage approvals for, or None for all resources."""
        user = self.request.user
        if user.is_superuser:
            return None

        user_groups = user.groups.all()
        managed_resource_ids = []
        for resource in Resource.objects.prefetch_related("review_groups"):
            if check_if_groups_in_review_groups(
                resource.review_groups.all(), user_groups, RESOURCE_APPROVAL_PERMISSION
            ):
                managed_resource_ids.append(resource.id)
        return managed_resource_ids

    def get_actionable_resource_approvals(self):
        """Return pending resource approvals this user may respond to."""
        approvals = ProjectPiChangeRequestResourceApproval.objects.filter(
            status__name="Pending", request__status__name__in=["New", "Awaiting Approvals"]
        ).select_related(
            "resource", "resource__resource_type", "request", "request__project", "request__status", "status"
        )

        managed_resource_ids = self.managed_resource_ids
        if managed_resource_ids is not None:
            approvals = approvals.filter(resource_id__in=managed_resource_ids)

        return approvals

    def get_history(self):
        """Combine request, resource approval, and user approval status changes into one list.

        Only records where a status was changed are included, plus request creation records so the
        initiator of a request is visible. Request and user approval history is shown to everyone;
        resource approval history is limited for non-superusers to resources they manage.
        """
        request_history = ProjectPiChangeRequest.history.select_related("project", "status", "history_user").filter(
            history_type__in=["+", "~"]
        )
        approval_history = ProjectPiChangeRequestResourceApproval.history.select_related(
            "resource", "request", "request__project", "status", "history_user"
        ).filter(history_type="~")
        user_approval_history = ProjectPiChangeRequestUserApproval.history.select_related(
            "request", "request__project", "status", "user", "history_user"
        ).filter(history_type="~")

        managed_resource_ids = self.managed_resource_ids
        if managed_resource_ids is not None:
            approval_history = approval_history.filter(resource_id__in=managed_resource_ids)

        entries = [self.get_request_history_entry(record) for record in request_history]
        entries += [self.get_approval_history_entry(record) for record in approval_history]
        entries += [self.get_user_approval_history_entry(record) for record in user_approval_history]
        entries.sort(key=lambda entry: entry["date"], reverse=True)
        return entries

    def get_request_history_entry(self, record):
        project_title = record.project.title if record.project else "Unknown project"
        if record.history_type == "+":
            description = f'PI change request for "{project_title}" created'
        else:
            description = f'PI change request for "{project_title}" status changed to {record.status}'
        return {"date": record.history_date, "description": description, "user": record.history_user}

    def get_approval_history_entry(self, record):
        resource_name = record.resource.name if record.resource else "Unknown resource"
        project_title = record.request.project.title if record.request else "Unknown project"
        description = f'Resource approval for "{resource_name}" on "{project_title}" status changed to {record.status}'
        return {"date": record.history_date, "description": description, "user": record.history_user}

    def get_user_approval_history_entry(self, record):
        project_title = record.request.project.title if record.request else "Unknown project"
        user = record.user if record.user else "Unknown user"
        description = f'User approval for {user} on "{project_title}" status changed to {record.status}'
        return {"date": record.history_date, "description": description, "user": record.history_user}

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        context["is_superuser"] = self.request.user.is_superuser
        context["pending_pi_change_requests"] = ProjectPiChangeRequest.objects.filter(
            status__name__in=["Awaiting Approvals", "Blocked", "Ready", "New"]
        ).select_related("project", "project__pi", "status", "new_pi")
        context["show_settings_link"] = self.request.user.is_superuser or bool(self.managed_resource_ids)
        context["pending_resource_approvals"] = self.get_actionable_resource_approvals()
        context["history"] = self.get_history()
        return context


class ProjectPiChangeRequestResourceApprovalSettingsView(LoginRequiredMixin, TemplateView):
    template_name = "pi_change_request/pi_change_request_resource_approval_settings.html"

    def get_resource_approvals_formset(self):
        settings = (
            ProjectPiChangeRequestResourceApprovalSetting.objects.select_related("resource", "resource__resource_type")
            .prefetch_related("resource__review_groups")
            .all()
        )
        settings = [
            {
                "pk": setting.pk,
                "resource": setting.resource,
                "requires_approval": setting.requires_approval,
                "review_groups": setting.resource.review_groups.all(),
            }
            for setting in settings
        ]
        user = self.request.user
        user_groups = user.groups.all()
        disable_selected = []
        for setting in settings:
            if user.is_superuser:
                can_edit = True
            else:
                can_edit = check_if_groups_in_review_groups(
                    setting.get("resource").review_groups.all(), user_groups, RESOURCE_APPROVAL_SETTING_PERMISSION
                )
            disable_selected.append(not can_edit)

        formset = formset_factory(
            ResourcesRequiringApprovalForm, max_num=len(settings), formset=ResourcesRequiringApprovalFormset
        )
        formset = formset(
            initial=settings,
            prefix="resourceapprovalform",
            form_kwargs={
                "disable_selected": disable_selected,
            },
        )
        return formset

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        context["resource_approvals_formset"] = self.get_resource_approvals_formset()
        return context


class ProjectPiChangeApprovalView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        if self.request.user.is_superuser:
            return True

    def dispatch(self, request, *args, **kwargs):
        self.pi_change_request = get_object_or_404(
            ProjectPiChangeRequest.objects.select_related("project", "current_pi", "new_pi", "initiator", "status"),
            pk=self.kwargs.get("pk"),
        )
        if not self.pi_change_request.status.name == "Ready":
            messages.error(
                request, f"Cannot approve a PI change request with status {self.pi_change_request.status.name}."
            )
            return redirect("pi-change-request-center")

        new_pi_is_active_manager = self.pi_change_request.project.projectuser_set.filter(
            user=self.pi_change_request.new_pi, status__name="Active", role__name="Manager"
        ).exists()
        if not new_pi_is_active_manager:
            messages.error(
                request,
                "Cannot approve a PI change request whose new PI is no longer an active manager on the project.",
            )
            return redirect("pi-change-request-center")

        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        return redirect("pi-change-request-center")

    def post(self, request, pk):
        pi_change_request = self.pi_change_request
        with transaction.atomic():
            pi_change_request.apply_pi_change()
            pi_change_request.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("Complete")
            pi_change_request.save()

        project_url = "{}{}".format(
            get_domain_url(request), reverse("project-detail", kwargs={"pk": pi_change_request.project.pk})
        )
        template_context = {
            "project_title": pi_change_request.project.title,
            "project_id": pi_change_request.project.pk,
            "new_pi": pi_change_request.new_pi,
            "project_url": project_url,
            "help_email": settings.EMAIL_TICKET_SYSTEM_ADDRESS,
        }
        receivers = set()
        for user in (pi_change_request.current_pi, pi_change_request.new_pi, pi_change_request.initiator):
            if user.email:
                receivers.add(user.email)

        send_email(
            "Your Project PI Change Request Was Approved",
            "pi_change_request/email/pi_change_request_approved.txt",
            template_context,
            receivers,
        )

        messages.success(request, "The PI change request has been approved.")
        return redirect("pi-change-request-center")


class ProjectPiChangeDenialView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        if self.request.user.is_superuser:
            return True

    def dispatch(self, request, *args, **kwargs):
        self.pi_change_request = get_object_or_404(
            ProjectPiChangeRequest.objects.select_related("project", "current_pi", "initiator", "status"),
            pk=self.kwargs.get("pk"),
        )
        if self.pi_change_request.status.name not in ["Awaiting Approvals", "Blocked", "Ready", "New"]:
            messages.error(
                request, f"Cannot deny a PI change request with status {self.pi_change_request.status.name}."
            )
            return redirect("pi-change-request-center")

        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        return redirect("pi-change-request-center")

    def post(self, request, pk):
        pi_change_request = self.pi_change_request
        with transaction.atomic():
            pi_change_request.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("Rejected")
            pi_change_request.save()

        project_url = "{}{}".format(
            get_domain_url(request), reverse("project-detail", kwargs={"pk": pi_change_request.project.pk})
        )
        template_context = {
            "project_title": pi_change_request.project.title,
            "project_id": pi_change_request.project.pk,
            "current_pi": pi_change_request.current_pi,
            "project_url": project_url,
            "help_email": settings.EMAIL_TICKET_SYSTEM_ADDRESS,
        }
        receivers = set()
        for user in (pi_change_request.current_pi, pi_change_request.new_pi, pi_change_request.initiator):
            if user.email:
                receivers.add(user.email)

        send_email(
            "Your Project PI Change Request Was Denied",
            "pi_change_request/email/pi_change_request_denied.txt",
            template_context,
            receivers,
        )

        messages.success(request, "The PI change request has been denied.")
        return redirect("pi-change-request-center")


class ProjectPiChangeDetailView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "pi_change_request/pi_change_request_detail.html"

    def test_func(self):
        if self.request.user.is_superuser:
            return True

    def get_context_data(self, *args, **kwargs):
        pi_change_request = get_object_or_404(
            ProjectPiChangeRequest.objects.select_related("project", "project__pi", "status", "new_pi"),
            pk=self.kwargs.get("pk"),
        )

        context = super().get_context_data(*args, **kwargs)
        context["pi_change_request"] = pi_change_request
        context["user_approvals"] = pi_change_request.user_approvals.select_related("user", "status")
        context["resource_approvals"] = pi_change_request.resource_approvals.select_related("resource", "status")
        return context


class ProjectPiChangeRequestResourceApprovalSettingView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        self.obj = get_object_or_404(
            ProjectPiChangeRequestResourceApprovalSetting, pk=request.POST.get("resource_approval_id")
        )

        user = self.request.user
        if user.is_superuser:
            return super().dispatch(request, *args, **kwargs)

        passed = check_if_groups_in_review_groups(
            self.obj.resource.review_groups.all(), user.groups.all(), RESOURCE_APPROVAL_SETTING_PERMISSION
        )
        if not passed:
            return HttpResponse("not permitted", status=403)

        return super().dispatch(request, *args, **kwargs)

    def post(self, request):
        obj = self.obj

        checked = request.POST.get("checked")
        http_message = ""
        if checked == "true":
            obj.requires_approval = True
            http_message = "checked"
        else:
            obj.requires_approval = False
            http_message = "unchecked"

        obj.save()
        return HttpResponse(http_message, status=200)


class ProjectPiChangeRequestUserApprovalView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "pi_change_request/pi_change_request_user_detail.html"

    def test_func(self):
        self.pi_change_user_request = get_object_or_404(
            ProjectPiChangeRequestUserApproval.objects.select_related("user", "request"), pk=self.kwargs.get("pk")
        )

        if self.request.user.is_superuser:
            return True

        if self.request.user == self.pi_change_user_request.user:
            return True

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        context["pi_change_user_request"] = self.pi_change_user_request
        context["can_respond"] = (
            self.request.user == self.pi_change_user_request.user
            and self.pi_change_user_request.status.name == "Pending"
        )
        return context


class ProjectPiChangeRequestUserResponseView(LoginRequiredMixin, UserPassesTestMixin, View):
    response_status = None
    success_message = ""

    def dispatch(self, request, *args, **kwargs):
        self.user_approval = get_object_or_404(
            ProjectPiChangeRequestUserApproval.objects.select_related("user", "request", "request__project", "status"),
            pk=self.kwargs.get("pk"),
        )
        return super().dispatch(request, *args, **kwargs)

    def test_func(self):
        if self.request.user.is_superuser:
            return True
        return self.request.user == self.user_approval.user

    def get(self, request, pk):
        return redirect("pi-change-request-user", pk=pk)

    def post(self, request, pk):
        approval = self.user_approval
        if approval.status.name != "Pending":
            messages.error(request, "You have already responded to this PI change request.")
            return redirect("pi-change-request-user", pk=pk)

        if approval.request.status.name not in ["New", "Awaiting Approvals"]:
            messages.error(request, "This PI change request is not accepting approvals.")
            return redirect("pi-change-request-user", pk=pk)

        approval.status = ProjectPiChangeRequestUserApprovalStatusChoice.objects.get_by_natural_key(
            self.response_status
        )
        with transaction.atomic():
            approval.save()
            approval.request.update_status_from_approvals()

        url = "{}{}".format(get_domain_url(request), reverse("pi-change-request-center"))
        template_context = {
            "project_title": approval.request.project.title,
            "project_id": approval.request.project.pk,
            "user": approval.user,
            "response": self.response_status,
            "url": url,
            "help_email": settings.EMAIL_TICKET_SYSTEM_ADDRESS,
        }
        send_email(
            "PI Change Request User Response",
            "pi_change_request/email/pi_change_request_user_response.txt",
            template_context,
        )

        if approval.request.status.name == "Ready":
            send_ready_email(approval.request, url)

        messages.success(request, self.success_message)
        return redirect("pi-change-request-user", pk=pk)


class ProjectPiChangeRequestUserApprovedView(ProjectPiChangeRequestUserResponseView):
    response_status = "Approved"
    success_message = "You have approved the PI change request."


class ProjectPiChangeRequestUserDeniedView(ProjectPiChangeRequestUserResponseView):
    response_status = "Denied"
    success_message = "You have declined the PI change request."


class ProjectPiChangeRequestResourceResponseView(LoginRequiredMixin, UserPassesTestMixin, View):
    response_status = None
    success_message = ""

    def dispatch(self, request, *args, **kwargs):
        self.resource_approval = get_object_or_404(
            ProjectPiChangeRequestResourceApproval.objects.select_related(
                "resource", "request", "request__project", "request__status", "status"
            ),
            pk=self.kwargs.get("pk"),
        )
        return super().dispatch(request, *args, **kwargs)

    def test_func(self):
        if self.request.user.is_superuser:
            return True

        return check_if_groups_in_review_groups(
            self.resource_approval.resource.review_groups.all(),
            self.request.user.groups.all(),
            RESOURCE_APPROVAL_PERMISSION,
        )

    def get(self, request, pk):
        return redirect("pi-change-request-center")

    def post(self, request, pk):
        approval = self.resource_approval
        if approval.status.name != "Pending":
            messages.error(request, "This resource approval has already been responded to.")
            return redirect("pi-change-request-center")

        if approval.request.status.name not in ["New", "Awaiting Approvals"]:
            messages.error(request, "This PI change request is not accepting approvals.")
            return redirect("pi-change-request-center")

        approval.status = ProjectPiChangeRequestResourceApprovalStatusChoice.objects.get_by_natural_key(
            self.response_status
        )
        approval.handler = request.user
        with transaction.atomic():
            approval.save()
            approval.request.update_status_from_approvals()

        url = "{}{}".format(get_domain_url(request), reverse("pi-change-request-center"))
        template_context = {
            "project_title": approval.request.project.title,
            "project_id": approval.request.project.pk,
            "resource": approval.resource,
            "handler": request.user,
            "response": self.response_status,
            "url": url,
            "help_email": settings.EMAIL_TICKET_SYSTEM_ADDRESS,
        }
        send_email(
            "PI Change Request Resource Response",
            "pi_change_request/email/pi_change_request_resource_response.txt",
            template_context,
        )

        if approval.request.status.name == "Ready":
            send_ready_email(approval.request, url)

        messages.success(request, self.success_message)
        return redirect("pi-change-request-center")


class ProjectPiChangeRequestResourceApprovedView(ProjectPiChangeRequestResourceResponseView):
    response_status = "Approved"
    success_message = "You have approved the resource."


class ProjectPiChangeRequestResourceDeniedView(ProjectPiChangeRequestResourceResponseView):
    response_status = "Denied"
    success_message = "You have denied the resource."
