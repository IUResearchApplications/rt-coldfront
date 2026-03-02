from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.urls import reverse
from django.views.generic import FormView

from coldfront.core.project.utils import check_if_pis_eligible
from coldfront.core.utils.common import import_from_settings
from coldfront.core.utils.mail import send_email_template
from coldfront.plugins.request_forms.forms import SoftwareRequestForm, StatsRequestForm

if "coldfront.plugins.ldap_misc" in settings.INSTALLED_APPS:
    from coldfront.plugins.ldap_misc.utils.project import check_if_pis_eligible

REQUEST_FORMS_EMAILS = import_from_settings("REQUEST_FORMS_EMAILS", {})
EMAIL_SENDER = import_from_settings("EMAIL_SENDER", "")


class SoftwareRequestView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    form_class = SoftwareRequestForm
    template_name = "request_forms/software_request.html"

    def test_func(self):
        user = self.request.user
        if user.is_superuser:
            return True

        if check_if_pis_eligible([user.username]).get(user.username, True):
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


class StatsRequestView(LoginRequiredMixin, FormView):
    form_class = StatsRequestForm
    template_name = "request_forms/stats_request.html"

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
