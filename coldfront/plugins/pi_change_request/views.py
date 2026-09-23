from functools import cached_property

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.generic import CreateView, TemplateView, View

from coldfront.core.project.models import Project
from coldfront.core.utils.common import get_domain_url
from coldfront.plugins.pi_change_request.forms import ProjectPiChangeRequestForm
from coldfront.plugins.pi_change_request.models import (
    ACTIVE_REQUEST_STATUSES,
    PI_CHANGE_DISALLOWED_PROJECT_STATUSES,
    ProjectPiChangeRequest,
    ProjectPiChangeRequestResourceApproval,
    ProjectPiChangeRequestResourceApprovalSetting,
    ProjectPiChangeRequestResourceApprovalStatusChoice,
    ProjectPiChangeRequestStatusChoice,
    ProjectPiChangeRequestUserApproval,
    ProjectPiChangeRequestUserApprovalStatusChoice,
)
from coldfront.plugins.pi_change_request.permissions import (
    PI_CHANGE_REQUEST_CHANGE_PERMISSION,
    PI_CHANGE_REQUEST_VIEW_PERMISSION,
    RESOURCE_APPROVAL_CHANGE_CODENAME,
    RESOURCE_APPROVAL_CHANGE_PERMISSION,
    RESOURCE_APPROVAL_SETTING_CHANGE_CODENAME,
    RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION,
    actionable_resource_approvals,
    managed_resource_ids,
    resource_actionable_by,
)
from coldfront.plugins.pi_change_request.signals import (
    pi_change_request_completed,
    pi_change_request_created,
    pi_change_request_resource_response,
    pi_change_request_user_response,
)
from coldfront.plugins.pi_change_request.utils import (
    full_name_with_username,
    get_participant_email_addresses,
    send_blocked_email,
    send_email,
    send_ready_email,
    send_resource_approval_notifications,
    send_slack_message,
    send_user_approval_notifications,
)


class SuperuserOrPermissionRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Grant access to superusers and to users holding the dotted permission."""

    required_permission = None

    def test_func(self):
        if self.request.user.is_superuser:
            return True
        return self.request.user.has_perm(self.required_permission)


class ProjectPiChangeRequestView(SuccessMessageMixin, LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = ProjectPiChangeRequest
    template_name_suffix = "_form"
    form_class = ProjectPiChangeRequestForm
    success_message = "Project PI change request received."

    @cached_property
    def project(self):
        return get_object_or_404(Project, pk=self.kwargs.get("pk"))

    def test_func(self):
        if self.project.status.name in PI_CHANGE_DISALLOWED_PROJECT_STATUSES:
            return False

        if self.request.user.is_superuser:
            return True

        project_obj = self.project
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
        context["project"] = self.project
        context["project_pk"] = self.kwargs.get("pk")
        return context

    def form_valid(self, form):
        request_obj = form.instance
        request_obj.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("New")
        request_obj.current_pi = request_obj.project.pi
        request_obj.initiator = self.request.user

        with transaction.atomic():
            # clean() cannot prevent two concurrent submissions, so lock the project row and
            # re-check for an active request after acquiring the lock.
            Project.objects.select_for_update().get(pk=self.project.pk)
            if ProjectPiChangeRequest.objects.filter(
                project=self.project, status__name__in=ACTIVE_REQUEST_STATUSES
            ).exists():
                form.add_error(None, "An active PI change request already exists for this project.")
                return self.form_invalid(form)

            response = super().form_valid(form)
            request_obj.set_resources_from_active_allocations()
            resource_approvals = request_obj.create_resource_approvals()
            user_approvals = request_obj.create_user_approvals([request_obj.current_pi, request_obj.new_pi])

        domain_url = get_domain_url(self.request)
        pi_change_request_created.send(sender=self.__class__, pi_change_request_pk=request_obj.pk)
        # The initiator's approval was recorded during creation; signal it like any other response.
        for approval in user_approvals:
            if approval.status.name == "Approved":
                pi_change_request_user_response.send(
                    sender=self.__class__,
                    user_approval_pk=approval.pk,
                    pi_change_request_pk=request_obj.pk,
                )

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
            "justification": request_obj.justification,
            "help_email": settings.EMAIL_TICKET_SYSTEM_ADDRESS,
        }
        send_email(
            "New Project PI Change Request",
            "pi_change_request/email/new_pi_change_request.txt",
            template_context,
            settings.EMAIL_TICKET_SYSTEM_ADDRESS,
        )

        send_user_approval_notifications(request_obj, user_approvals, domain_url)
        send_resource_approval_notifications(
            request_obj, resource_approvals, domain_url, RESOURCE_APPROVAL_CHANGE_PERMISSION
        )

        return response

    def get_success_url(self):
        return self.object.project.get_absolute_url()


class ProjectPiChangeRequestCenterView(SuperuserOrPermissionRequiredMixin, TemplateView):
    required_permission = PI_CHANGE_REQUEST_VIEW_PERMISSION
    template_name = "pi_change_request/pi_change_request_center.html"

    @cached_property
    def managed_resource_ids(self):
        """Cached per-request lookup of the resources this user may manage approvals for."""
        return managed_resource_ids(self.request.user)

    def get_actionable_resource_approvals(self):
        """Return pending resource approvals this user may respond to."""
        return actionable_resource_approvals(self.request.user).select_related(
            "resource", "resource__resource_type", "request", "request__project", "request__status", "status"
        )

    def get_history(self):
        """Combine request, resource approval, and user approval status changes into one list.

        Only records where a status was actually changed are included, plus request creation records
        so the initiator of a request is visible. Request and user approval history is shown to
        everyone; resource approval history is limited for non-superusers to resources they manage.
        """
        request_history = (
            ProjectPiChangeRequest.history.select_related("project", "status", "history_user")
            .filter(history_type__in=["+", "~"])
            .order_by("id", "history_id")
        )
        approval_history = (
            ProjectPiChangeRequestResourceApproval.history.select_related(
                "resource", "request", "request__project", "status", "history_user"
            )
            .filter(history_type="~")
            .order_by("id", "history_id")
        )
        user_approval_history = (
            ProjectPiChangeRequestUserApproval.history.select_related(
                "request", "request__project", "status", "user", "history_user"
            )
            .filter(history_type="~")
            .order_by("id", "history_id")
        )

        managed_resource_ids = self.managed_resource_ids
        if managed_resource_ids is not None:
            approval_history = approval_history.filter(resource_id__in=managed_resource_ids)

        entries = [
            self.get_request_history_entry(record) for record in self.get_status_changed_records(request_history)
        ]
        entries += [
            self.get_approval_history_entry(record) for record in self.get_status_changed_records(approval_history)
        ]
        entries += [
            self.get_user_approval_history_entry(record)
            for record in self.get_status_changed_records(user_approval_history)
        ]
        entries.sort(key=lambda entry: entry["date"], reverse=True)
        return entries

    def get_status_changed_records(self, records):
        """Yield creation records and records where the status differs from the object's previous state.

        simple_history writes a "~" record on every save, even when only an unrelated field was
        edited, so each record is compared with the previous one for the same object. The record's id
        field holds the original object's pk, so records must be ordered by id and history id.
        """
        previous_object_id = None
        previous_status_id = None
        for record in records:
            if record.id != previous_object_id:
                previous_object_id = record.id
                previous_status_id = None
            if record.history_type == "+" or record.status_id != previous_status_id:
                yield record
            previous_status_id = record.status_id

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
        context["pending_pi_change_requests"] = ProjectPiChangeRequest.objects.filter(
            status__name__in=ACTIVE_REQUEST_STATUSES
        ).select_related("project", "project__pi", "status", "new_pi")
        context["show_settings_link"] = self.request.user.is_superuser or self.request.user.has_perm(
            RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION
        )
        context["can_change_requests"] = self.request.user.is_superuser or self.request.user.has_perm(
            PI_CHANGE_REQUEST_CHANGE_PERMISSION
        )
        context["pending_resource_approvals"] = self.get_actionable_resource_approvals()
        context["history"] = self.get_history()
        return context


class ProjectPiChangeRequestResourceApprovalSettingsView(SuperuserOrPermissionRequiredMixin, TemplateView):
    required_permission = RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION
    template_name = "pi_change_request/pi_change_request_resource_approval_settings.html"

    def get_resource_approval_settings(self):
        """Return one row per resource approval setting, including whether the current user may toggle it."""
        user = self.request.user
        user_groups = user.groups.all()
        rows = []
        approval_settings = (
            ProjectPiChangeRequestResourceApprovalSetting.objects.select_related("resource", "resource__resource_type")
            .prefetch_related("resource__review_groups")
            .all()
        )
        for setting in approval_settings:
            can_edit = user.is_superuser or resource_actionable_by(
                user, user_groups, setting.resource, RESOURCE_APPROVAL_SETTING_CHANGE_CODENAME
            )
            rows.append(
                {
                    "pk": setting.pk,
                    "resource": setting.resource,
                    "requires_approval": setting.requires_approval,
                    "review_groups": setting.resource.review_groups.all(),
                    "can_edit": can_edit,
                }
            )
        return rows

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        context["resource_approval_settings"] = self.get_resource_approval_settings()
        return context


class ProjectPiChangeAdminActionView(SuperuserOrPermissionRequiredMixin, View):
    """Shared flow for center staff actions (activate or deny) on a PI change request.

    The request's state is checked in dispatch for an early redirect, then re-checked under
    the request row lock in post, since the state can change between the two.
    """

    required_permission = PI_CHANGE_REQUEST_CHANGE_PERMISSION
    success_message = ""

    def state_error(self, pi_change_request):
        """Return an error message when the request is not in a state allowing this action."""
        raise NotImplementedError

    def perform_action(self, pi_change_request):
        """Apply the state transition; runs inside the post transaction."""
        raise NotImplementedError

    def send_notifications(self, request, pi_change_request):
        """Notify the involved parties once the action has been applied."""
        raise NotImplementedError

    def dispatch(self, request, *args, **kwargs):
        self.pi_change_request = get_object_or_404(
            ProjectPiChangeRequest.objects.select_related("project", "current_pi", "new_pi", "initiator", "status"),
            pk=self.kwargs.get("pk"),
        )
        error = self.state_error(self.pi_change_request)
        if error:
            messages.error(request, error)
            return redirect("pi-change-request-center")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        return redirect("pi-change-request-center")

    def post(self, request, pk):
        with transaction.atomic():
            pi_change_request = ProjectPiChangeRequest.objects.select_for_update().get(pk=pk)
            error = self.state_error(pi_change_request)
            if error:
                messages.error(request, error)
                return redirect("pi-change-request-center")
            self.perform_action(pi_change_request)

        pi_change_request_completed.send(sender=self.__class__, pi_change_request_pk=pi_change_request.pk)
        self.send_notifications(request, pi_change_request)

        messages.success(request, self.success_message)
        return redirect("pi-change-request-center")


class ProjectPiChangeApprovalView(ProjectPiChangeAdminActionView):
    success_message = "The PI change request has been approved."

    def state_error(self, pi_change_request):
        if not pi_change_request.is_ready:
            return f"Cannot approve a PI change request with status {pi_change_request.status.name}."
        if not pi_change_request.is_new_pi_active_manager:
            return "Cannot approve a PI change request whose new PI is no longer an active manager on the project."
        return None

    def perform_action(self, pi_change_request):
        pi_change_request.apply_pi_change()
        pi_change_request.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("Complete")
        pi_change_request.save()
        pi_change_request.cancel_pending_approvals()

    def send_notifications(self, request, pi_change_request):
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
        receivers = get_participant_email_addresses(pi_change_request)

        send_email(
            "Your Project PI Change Request Was Approved",
            "pi_change_request/email/pi_change_request_approved.txt",
            template_context,
            receivers,
        )


class ProjectPiChangeDenialView(ProjectPiChangeAdminActionView):
    success_message = "The PI change request has been denied."

    def state_error(self, pi_change_request):
        if not pi_change_request.is_denyable:
            return f"Cannot deny a PI change request with status {pi_change_request.status.name}."
        return None

    def perform_action(self, pi_change_request):
        pi_change_request.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("Rejected")
        pi_change_request.save()
        pi_change_request.cancel_pending_approvals()

    def send_notifications(self, request, pi_change_request):
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
        receivers = get_participant_email_addresses(pi_change_request)

        send_email(
            "Your Project PI Change Request Was Denied",
            "pi_change_request/email/pi_change_request_denied.txt",
            template_context,
            receivers,
        )


class ProjectPiChangeDetailView(SuperuserOrPermissionRequiredMixin, TemplateView):
    required_permission = PI_CHANGE_REQUEST_VIEW_PERMISSION
    template_name = "pi_change_request/pi_change_request_detail.html"

    def get_context_data(self, *args, **kwargs):
        pi_change_request = get_object_or_404(
            ProjectPiChangeRequest.objects.select_related("project", "status", "current_pi", "new_pi"),
            pk=self.kwargs.get("pk"),
        )

        context = super().get_context_data(*args, **kwargs)
        context["pi_change_request"] = pi_change_request
        context["user_approvals"] = pi_change_request.user_approvals.select_related("user", "status")
        context["resource_approvals"] = pi_change_request.resource_approvals.select_related(
            "resource", "resource__resource_type", "status"
        )
        can_change_requests = self.request.user.is_superuser or self.request.user.has_perm(
            PI_CHANGE_REQUEST_CHANGE_PERMISSION
        )
        context["can_activate"] = can_change_requests and pi_change_request.is_ready
        context["can_deny"] = can_change_requests and pi_change_request.is_denyable
        return context


class ProjectPiChangeRequestResourceApprovalSettingView(SuperuserOrPermissionRequiredMixin, View):
    required_permission = RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST":
            self.obj = get_object_or_404(
                ProjectPiChangeRequestResourceApprovalSetting, pk=request.POST.get("resource_approval_id")
            )

            user = self.request.user
            if not user.is_superuser:
                if not resource_actionable_by(
                    user, user.groups.all(), self.obj.resource, RESOURCE_APPROVAL_SETTING_CHANGE_CODENAME
                ):
                    return HttpResponse("not permitted", status=403)

        return super().dispatch(request, *args, **kwargs)

    def post(self, request):
        obj = self.obj

        checked = request.POST.get("checked")
        if checked not in ("true", "false"):
            return HttpResponse("Invalid value for 'checked'.", status=400)

        obj.requires_approval = checked == "true"
        obj.save()
        return HttpResponse("checked" if checked == "true" else "unchecked", status=200)


class ProjectPiChangeRequestUserApprovalView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "pi_change_request/pi_change_request_user_detail.html"

    def test_func(self):
        self.pi_change_user_request = get_object_or_404(
            ProjectPiChangeRequestUserApproval.objects.select_related("user", "request", "request__status"),
            pk=self.kwargs.get("pk"),
        )

        if self.request.user.is_superuser:
            return True

        return self.request.user == self.pi_change_user_request.user

    def get_context_data(self, *args, **kwargs):
        approval = self.pi_change_user_request
        request_open = approval.request.status.name in ["New", "Awaiting Approvals"]
        context = super().get_context_data(*args, **kwargs)
        context["pi_change_user_request"] = approval
        context["request_open"] = request_open
        context["help_email"] = settings.EMAIL_TICKET_SYSTEM_ADDRESS
        context["can_respond"] = (
            self.request.user == approval.user and approval.status.name == "Pending" and request_open
        )
        return context


class ProjectPiChangeRequestResponseView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Shared flow for responding to a pending user or resource approval on a PI change request.

    Subclasses fetch their approval in get_approval, decide who may respond in test_func, and
    fill in the response-specific hooks. The request row is locked while the response is
    recorded so concurrent responses cannot race the status recomputation.
    """

    response_status = None
    success_message = ""
    already_responded_message = ""

    def get_approval(self):
        raise NotImplementedError

    def approval_redirect(self, pk):
        raise NotImplementedError

    def record_response(self, approval, request):
        raise NotImplementedError

    def validate_response(self, request):
        """Check the submitted response; return an error message, or None to record it."""
        return None

    def send_response_email(self, request, pi_change_request, url):
        raise NotImplementedError

    def send_response_signal(self, approval, pi_change_request):
        """Signal the recorded response so other systems can react to it."""
        raise NotImplementedError

    def blocked_reason(self, approval):
        raise NotImplementedError

    def dispatch(self, request, *args, **kwargs):
        self.approval = self.get_approval()
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        return self.approval_redirect(pk)

    def post(self, request, pk):
        approval = self.approval
        with transaction.atomic():
            pi_change_request = ProjectPiChangeRequest.objects.select_for_update().get(pk=approval.request_id)
            approval.refresh_from_db()

            if approval.status.name != "Pending":
                messages.error(request, self.already_responded_message)
                return self.approval_redirect(pk)

            if pi_change_request.status.name not in ["New", "Awaiting Approvals"]:
                messages.error(request, "This PI change request is not accepting approvals.")
                return self.approval_redirect(pk)

            error_message = self.validate_response(request)
            if error_message:
                messages.error(request, error_message)
                return self.approval_redirect(pk)

            self.record_response(approval, request)
            pi_change_request.update_status_from_approvals()

        url = "{}{}".format(get_domain_url(request), reverse("pi-change-request-center"))
        self.send_response_signal(approval, pi_change_request)
        self.send_response_email(request, pi_change_request, url)

        if pi_change_request.status.name == "Ready":
            send_ready_email(pi_change_request, url)
        elif pi_change_request.status.name == "Blocked":
            send_blocked_email(pi_change_request, get_domain_url(request), self.blocked_reason(approval))

        messages.success(request, self.success_message)
        return self.approval_redirect(pk)


class ProjectPiChangeRequestUserResponseView(ProjectPiChangeRequestResponseView):
    """A user approval is responded to by its assigned user or by a superuser."""

    already_responded_message = "You have already responded to this PI change request."

    def get_approval(self):
        return get_object_or_404(
            ProjectPiChangeRequestUserApproval.objects.select_related("user", "request", "request__project", "status"),
            pk=self.kwargs.get("pk"),
        )

    def test_func(self):
        if self.request.user.is_superuser:
            return True
        return self.request.user == self.approval.user

    def approval_redirect(self, pk):
        return redirect("pi-change-request-user", pk=pk)

    def record_response(self, approval, request):
        approval.status = ProjectPiChangeRequestUserApprovalStatusChoice.objects.get_by_natural_key(
            self.response_status
        )
        approval.reason = request.POST.get("reason", "").strip()
        approval.save()

    def send_response_signal(self, approval, pi_change_request):
        pi_change_request_user_response.send(
            sender=self.__class__,
            user_approval_pk=approval.pk,
            pi_change_request_pk=pi_change_request.pk,
        )

    def send_response_email(self, request, pi_change_request, url):
        template_context = {
            "project_title": pi_change_request.project.title,
            "project_id": pi_change_request.project.pk,
            "user": self.approval.user,
            "response": self.response_status,
            "reason": self.approval.reason,
            "url": url,
            "help_email": settings.EMAIL_TICKET_SYSTEM_ADDRESS,
        }
        send_email(
            "PI Change Request User Response",
            "pi_change_request/email/pi_change_request_user_response.txt",
            template_context,
        )

    def blocked_reason(self, approval):
        reason = f": {approval.reason}" if approval.reason else ""
        return f"{full_name_with_username(approval.user)} declined the change{reason}"


class ProjectPiChangeRequestUserApprovedView(ProjectPiChangeRequestUserResponseView):
    response_status = "Approved"
    success_message = "You have approved the PI change request."


class ProjectPiChangeRequestUserDeniedView(ProjectPiChangeRequestUserResponseView):
    response_status = "Denied"
    success_message = "You have declined the PI change request."

    def validate_response(self, request):
        if not request.POST.get("reason", "").strip():
            return "Please provide a reason for declining this PI change request."
        return None


class ProjectPiChangeRequestResourceResponseView(ProjectPiChangeRequestResponseView):
    """A resource approval is responded to by members of the resource's review groups."""

    already_responded_message = "This resource approval has already been responded to."

    def get_approval(self):
        return get_object_or_404(
            ProjectPiChangeRequestResourceApproval.objects.select_related(
                "resource", "request", "request__project", "request__status", "status"
            ),
            pk=self.kwargs.get("pk"),
        )

    def test_func(self):
        if self.request.user.is_superuser:
            return True

        return resource_actionable_by(
            self.request.user,
            self.request.user.groups.all(),
            self.approval.resource,
            RESOURCE_APPROVAL_CHANGE_CODENAME,
        )

    def approval_redirect(self, pk):
        return redirect("pi-change-request-center")

    def record_response(self, approval, request):
        approval.status = ProjectPiChangeRequestResourceApprovalStatusChoice.objects.get_by_natural_key(
            self.response_status
        )
        approval.handler = request.user
        approval.reason = request.POST.get("reason", "").strip()
        approval.save()

    def send_response_signal(self, approval, pi_change_request):
        pi_change_request_resource_response.send(
            sender=self.__class__,
            resource_approval_pk=approval.pk,
            pi_change_request_pk=pi_change_request.pk,
        )

    def send_response_email(self, request, pi_change_request, url):
        template_context = {
            "project_title": pi_change_request.project.title,
            "project_id": pi_change_request.project.pk,
            "resource": self.approval.resource,
            "handler": request.user,
            "response": self.response_status,
            "reason": self.approval.reason,
            "url": url,
            "help_email": settings.EMAIL_TICKET_SYSTEM_ADDRESS,
        }
        send_email(
            "PI Change Request Resource Response",
            "pi_change_request/email/pi_change_request_resource_response.txt",
            template_context,
        )

    def blocked_reason(self, approval):
        reason = f": {approval.reason}" if approval.reason else ""
        return f'the approval for "{approval.resource}" was denied{reason}'


class ProjectPiChangeRequestResourceApprovedView(ProjectPiChangeRequestResourceResponseView):
    response_status = "Approved"
    success_message = "You have approved the resource."


class ProjectPiChangeRequestResourceDeniedView(ProjectPiChangeRequestResourceResponseView):
    response_status = "Denied"
    success_message = "You have denied the resource."
    template_name = "pi_change_request/pi_change_request_resource_deny.html"

    def get(self, request, pk):
        """Render a confirmation page collecting an optional denial reason."""
        return render(request, self.template_name, {"approval": self.approval})
