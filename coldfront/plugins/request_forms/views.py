from django.urls import reverse
from django.contrib import messages
from django.views.generic import FormView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

from coldfront.core.utils.common import import_from_settings
from coldfront.core.utils.mail import send_email_template
from coldfront.plugins.request_forms.forms import SoftwareRequestForm, StatsRequestForm
from coldfront.plugins.ldap_user_info.utils import get_user_info


REQUEST_FORMS_EMAILS = import_from_settings("REQUEST_FORMS_EMAILS", {})
EMAIL_SENDER = import_from_settings("EMAIL_SENDER", "")
PROJECT_PI_ELIGIBLE_ADS_GROUPS = import_from_settings("PROJECT_PI_ELIGIBLE_ADS_GROUPS", "")


class SoftwareRequestView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    form_class = SoftwareRequestForm
    template_name = "request_forms/software_request.html"

    def test_func(self):
        if self.request.user.is_superuser:
            return True

        memberships = get_user_info(self.request.user.username, ["memberOf"]).get("memberOf")
        for membership in memberships:
            if membership in PROJECT_PI_ELIGIBLE_ADS_GROUPS:
                return True

        messages.error(self.request, "Only Staff or Faculty can request HPC software.")

    def form_valid(self, form):
        send_email_template(
            "HPC Software Request",
            "request_forms/email/software_request.txt",
            {"user": self.request.user, **form.cleaned_data},
            self.request.user.email,
            REQUEST_FORMS_EMAILS.get("software_form", ""),
        )
        messages.success(
            self.request,
            "We received your software request. You will hear back from us within 2 business days.",
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("home")


class StatsRequestView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    form_class = StatsRequestForm
    template_name = "request_forms/stats_request.html"

    def test_func(self):
        return True

    def form_valid(self, form):
        send_email_template(
            "Stats Request",
            "request_forms/email/stats_request.txt",
            {"user": self.request.user, **form.cleaned_data},
            self.request.user.email,
            REQUEST_FORMS_EMAILS.get("stats_form", ""),
        )
        messages.success(
            self.request,
            "We received your stats request. You will hear back from us within 2 business days.",
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("home")
