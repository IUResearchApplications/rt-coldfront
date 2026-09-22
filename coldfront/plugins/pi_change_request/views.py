from functools import cached_property

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.messages.views import SuccessMessageMixin
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
from coldfront.plugins.pi_change_request.utils import (
    send_blocked_email,
    send_email,
    send_ready_email,
    send_resource_approval_notifications,
    send_slack_message,
    send_user_approval_notifications,
)

RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION = "pi_change_request.change_projectpichangerequestresourceapprovalsetting"
RESOURCE_APPROVAL_CHANGE_PERMISSION = "pi_change_request.change_projectpichangerequestresourceapproval"
PI_CHANGE_REQUEST_VIEW_PERMISSION = "pi_change_request.view_projectpichangerequest"
PI_CHANGE_REQUEST_CHANGE_PERMISSION = "pi_change_request.change_projectpichangerequest"

# check_if_groups_in_review_groups matches bare codenames, unlike has_perm which takes the dotted form.
RESOURCE_APPROVAL_SETTING_CHANGE_CODENAME = RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION.rpartition(".")[2]
RESOURCE_APPROVAL_CHANGE_CODENAME = RESOURCE_APPROVAL_CHANGE_PERMISSION.rpartition(".")[2]


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
            request_obj.resources.set(
                request_obj.project.allocation_set.filter(status__name="Active").values_list("resources", flat=True)
            )
            resource_approvals = request_obj.create_resource_approvals()
            user_approvals = request_obj.create_user_approvals([request_obj.current_pi, request_obj.new_pi])

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


class ProjectPiChangeRequestCenterView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "pi_change_request/pi_change_request_center.html"

    def test_func(self):
        if self.request.user.is_superuser:
            return True

        if self.request.user.has_perm(PI_CHANGE_REQUEST_VIEW_PERMISSION):
            return True

    @cached_property
    def managed_resource_ids(self):
        """Return ids of resources this user may manage approvals for, or None for all resources.

        A resource is manageable if the user belongs to one of its review groups holding the
        resource approval change permission, or if the resource has no review groups at all.
        """
        user = self.request.user
        if user.is_superuser:
            return None

        user_group_ids = list(user.groups.values_list("id", flat=True))
        if not user_group_ids:
            return []

        managed_resource_ids = set(
            Resource.objects.filter(
                review_groups__id__in=user_group_ids,
                review_groups__permissions__codename=RESOURCE_APPROVAL_CHANGE_CODENAME,
            ).values_list("id", flat=True)
        )
        managed_resource_ids.update(Resource.objects.filter(review_groups__isnull=True).values_list("id", flat=True))
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


class ProjectPiChangeRequestResourceApprovalSettingsView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "pi_change_request/pi_change_request_resource_approval_settings.html"

    def test_func(self):
        if self.request.user.is_superuser:
            return True

        if self.request.user.has_perm(RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION):
            return True

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
                    setting.get("resource").review_groups.all(),
                    user_groups,
                    RESOURCE_APPROVAL_SETTING_CHANGE_CODENAME,
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

        if self.request.user.has_perm(PI_CHANGE_REQUEST_CHANGE_PERMISSION):
            return True

    def dispatch(self, request, *args, **kwargs):
        self.pi_change_request = get_object_or_404(
            ProjectPiChangeRequest.objects.select_related("project", "current_pi", "new_pi", "initiator", "status"),
            pk=self.kwargs.get("pk"),
        )
        if not self.pi_change_request.is_ready:
            messages.error(
                request, f"Cannot approve a PI change request with status {self.pi_change_request.status.name}."
            )
            return redirect("pi-change-request-center")

        if not self.pi_change_request.is_new_pi_active_manager:
            messages.error(
                request,
                "Cannot approve a PI change request whose new PI is no longer an active manager on the project.",
            )
            return redirect("pi-change-request-center")

        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        return redirect("pi-change-request-center")

    def post(self, request, pk):
        with transaction.atomic():
            pi_change_request = ProjectPiChangeRequest.objects.select_for_update().get(pk=pk)
            if not pi_change_request.is_ready:
                messages.error(
                    request, f"Cannot approve a PI change request with status {pi_change_request.status.name}."
                )
                return redirect("pi-change-request-center")

            if not pi_change_request.is_new_pi_active_manager:
                messages.error(
                    request,
                    "Cannot approve a PI change request whose new PI is no longer an active manager on the project.",
                )
                return redirect("pi-change-request-center")

            pi_change_request.apply_pi_change()
            pi_change_request.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("Complete")
            pi_change_request.save()
            pi_change_request.cancel_pending_approvals()

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

        if self.request.user.has_perm(PI_CHANGE_REQUEST_CHANGE_PERMISSION):
            return True

    def dispatch(self, request, *args, **kwargs):
        self.pi_change_request = get_object_or_404(
            ProjectPiChangeRequest.objects.select_related("project", "current_pi", "initiator", "status"),
            pk=self.kwargs.get("pk"),
        )
        if not self.pi_change_request.is_denyable:
            messages.error(
                request, f"Cannot deny a PI change request with status {self.pi_change_request.status.name}."
            )
            return redirect("pi-change-request-center")

        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        return redirect("pi-change-request-center")

    def post(self, request, pk):
        with transaction.atomic():
            pi_change_request = ProjectPiChangeRequest.objects.select_for_update().get(pk=pk)
            if not pi_change_request.is_denyable:
                messages.error(request, f"Cannot deny a PI change request with status {pi_change_request.status.name}.")
                return redirect("pi-change-request-center")

            pi_change_request.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key("Rejected")
            pi_change_request.save()
            pi_change_request.cancel_pending_approvals()

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

        if self.request.user.has_perm(PI_CHANGE_REQUEST_VIEW_PERMISSION):
            return True

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


class ProjectPiChangeRequestResourceApprovalSettingView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        if self.request.user.is_superuser:
            return True

        if self.request.user.has_perm(RESOURCE_APPROVAL_SETTING_CHANGE_PERMISSION):
            return True

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST":
            self.obj = get_object_or_404(
                ProjectPiChangeRequestResourceApprovalSetting, pk=request.POST.get("resource_approval_id")
            )

            user = self.request.user
            if not user.is_superuser:
                passed = check_if_groups_in_review_groups(
                    self.obj.resource.review_groups.all(), user.groups.all(), RESOURCE_APPROVAL_SETTING_CHANGE_CODENAME
                )
                if not passed:
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
        with transaction.atomic():
            pi_change_request = ProjectPiChangeRequest.objects.select_for_update().get(pk=approval.request_id)
            approval.refresh_from_db()

            if approval.status.name != "Pending":
                messages.error(request, "You have already responded to this PI change request.")
                return redirect("pi-change-request-user", pk=pk)

            if pi_change_request.status.name not in ["New", "Awaiting Approvals"]:
                messages.error(request, "This PI change request is not accepting approvals.")
                return redirect("pi-change-request-user", pk=pk)

            approval.status = ProjectPiChangeRequestUserApprovalStatusChoice.objects.get_by_natural_key(
                self.response_status
            )
            approval.save()
            pi_change_request.update_status_from_approvals()

        url = "{}{}".format(get_domain_url(request), reverse("pi-change-request-center"))
        template_context = {
            "project_title": pi_change_request.project.title,
            "project_id": pi_change_request.project.pk,
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

        if pi_change_request.status.name == "Ready":
            send_ready_email(pi_change_request, url)
        elif pi_change_request.status.name == "Blocked":
            send_blocked_email(pi_change_request, get_domain_url(request), f"{approval.user} declined the change")

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
            RESOURCE_APPROVAL_CHANGE_CODENAME,
        )

    def get(self, request, pk):
        return redirect("pi-change-request-center")

    def post(self, request, pk):
        approval = self.resource_approval
        with transaction.atomic():
            pi_change_request = ProjectPiChangeRequest.objects.select_for_update().get(pk=approval.request_id)
            approval.refresh_from_db()

            if approval.status.name != "Pending":
                messages.error(request, "This resource approval has already been responded to.")
                return redirect("pi-change-request-center")

            if pi_change_request.status.name not in ["New", "Awaiting Approvals"]:
                messages.error(request, "This PI change request is not accepting approvals.")
                return redirect("pi-change-request-center")

            approval.status = ProjectPiChangeRequestResourceApprovalStatusChoice.objects.get_by_natural_key(
                self.response_status
            )
            approval.handler = request.user
            approval.save()
            pi_change_request.update_status_from_approvals()

        url = "{}{}".format(get_domain_url(request), reverse("pi-change-request-center"))
        template_context = {
            "project_title": pi_change_request.project.title,
            "project_id": pi_change_request.project.pk,
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

        if pi_change_request.status.name == "Ready":
            send_ready_email(pi_change_request, url)
        elif pi_change_request.status.name == "Blocked":
            send_blocked_email(
                pi_change_request, get_domain_url(request), f'the approval for "{approval.resource}" was denied'
            )

        messages.success(request, self.success_message)
        return redirect("pi-change-request-center")


class ProjectPiChangeRequestResourceApprovedView(ProjectPiChangeRequestResourceResponseView):
    response_status = "Approved"
    success_message = "You have approved the resource."


class ProjectPiChangeRequestResourceDeniedView(ProjectPiChangeRequestResourceResponseView):
    response_status = "Denied"
    success_message = "You have denied the resource."
