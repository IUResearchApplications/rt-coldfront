from functools import cached_property

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.forms.formsets import formset_factory
from django.shortcuts import HttpResponse, get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import CreateView, TemplateView, View

from coldfront.core.project.models import Project
from coldfront.core.utils.common import get_domain_url
from coldfront.core.utils.groups import check_if_groups_in_review_groups
from coldfront.plugins.pi_change_request.forms import (
    ProjectPiChangeRequestForm,
    ResourcesRequiringApprovalForm,
    ResourcesRequiringApprovalFormset,
)
from coldfront.plugins.pi_change_request.models import (
    ProjectPiChangeRequest,
    ProjectPiChangeRequestResourceApprovalSetting,
    ProjectPiChangeRequestStatusChoice,
)
from coldfront.plugins.pi_change_request.utils import send_email, send_slack_message


class ProjectPiChangeRequestView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = ProjectPiChangeRequest
    template_name_suffix = "_form"
    form_class = ProjectPiChangeRequestForm
    success_message = "Project updated."

    @cached_property
    def project(self):
        return get_object_or_404(Project, pk=self.kwargs.get("pk"))

    def test_func(self):
        if self.request.user.is_superuser:
            return True

        project_obj = self.project
        if self.request.user == project_obj.pi:
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
        response = super().form_valid(form)

        request_obj.resources.set(
            request_obj.project.allocation_set.filter(status__name="Active").values_list("resources", flat=True)
        )
        request_obj.create_resource_approvals()

        domain_url = get_domain_url(self.request)
        project_review_url = reverse("pi-change-request-center")
        url = "{}{}".format(domain_url, project_review_url)
        send_slack_message(self.project, url)

        template_context = {"url": url, "project_title": self.project.title, "project_id": self.project.pk}
        send_email(
            "New Project PI Change Request", "pi_change_request/email/new_pi_change_request.txt", template_context
        )

        return response

    def get_success_url(self):
        return self.object.project.get_absolute_url()


class ProjectPiChangeRequestCenterView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "pi_change_request/pi_change_request_center.html"

    def test_func(self):
        if self.request.user.is_superuser:
            return True

    def get_resource_approvals_formset(self):
        settings = (
            ProjectPiChangeRequestResourceApprovalSetting.objects.select_related("resource")
            .prefetch_related("resource__review_groups")
            .all()
        )
        settings = [
            {"pk": setting.pk, "resource": setting.resource, "requires_approval": setting.requires_approval}
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
                    "pi_change_request.change_projectpichangerequestresourceapprovalsetting",
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
        context["pending_pi_change_requests"] = ProjectPiChangeRequest.objects.filter(
            status__name__in=["Awaiting Approvals", "Blocked", "Ready", "New"]
        ).select_related("project", "project__pi", "status", "new_pi")
        context["resource_approvals_formset"] = self.get_resource_approvals_formset()
        return context


class ProjectPiChangeApprovalView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        if self.request.user.is_superuser:
            return True

    def dispatch(self, request, *args, **kwargs):
        self.pi_change_request = get_object_or_404(
            ProjectPiChangeRequest.objects.select_related("status"), pk=self.kwargs.get("pk")
        )
        if self.pi_change_request.status.name not in ["Ready", "New"]:
            messages.error(
                request, f"Cannot approve a PI change request with status {self.pi_change_request.status.name}."
            )
            return redirect("pi-change-request-center")

        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        return redirect("pi-change-request-center")


class ProjectPiChangeDenialView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        if self.request.user.is_superuser:
            return True

    def dispatch(self, request, *args, **kwargs):
        self.pi_change_request = get_object_or_404(
            ProjectPiChangeRequest.objects.select_related("status"), pk=self.kwargs.get("pk")
        )
        if self.pi_change_request.status.name not in ["Awaiting Approvals", "Blocked", "Ready", "New"]:
            messages.error(
                request, f"Cannot deny a PI change request with status {self.pi_change_request.status.name}."
            )
            return redirect("pi-change-request-center")

        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
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
            self.obj.resource.review_groups.all(),
            user.groups.all(),
            "pi_change_request.change_projectpichangerequestresourceapprovalsetting",
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
