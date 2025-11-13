from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import render
from django.urls import reverse
from django.views.generic import CreateView, ListView, UpdateView

from coldfront.plugins.maintenance_mode.models import Maintenance


class MaintenanceCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Maintenance
    fields = [
        "title",
        "prior_message",
        "during_message",
        "start_date_time",
        "end_date_time",
        "notification_days",
        "type",
    ]

    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True

    def get_success_url(self):
        return reverse("maintenance-list")


class MaintenanceListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = Maintenance
    paginate_by = 10

    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True

    def get_queryset(self):
        return super().get_queryset().select_related("type")


class MaintenanceUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Maintenance
    fields = [
        "title",
        "prior_message",
        "during_message",
        "start_date_time",
        "end_date_time",
        "notification_days",
        "type",
    ]

    def test_func(self):
        """UserPassesTestMixin Tests"""
        if self.request.user.is_superuser:
            return True

    def get_queryset(self):
        return super().get_queryset().select_related("type")

    def get_success_url(self):
        return reverse("maintenance-list")
