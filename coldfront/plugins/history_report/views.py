import logging

from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.views.generic import TemplateView

from coldfront.core.allocation.models import Allocation
from coldfront.plugins.history_report.forms import HistoryReportForm

logger = logging.getLogger(__name__)


class HistoryReportView(TemplateView):
    template_name = "history_report/history_report.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = HistoryReportForm()
        return context


class HistoryReportResultsView(TemplateView):
    template_name = "history_report/history_report_results.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        allocation = get_object_or_404(Allocation, pk=5)
        history = {}
        
        for record in allocation.history.all():
            prev_record = record.prev_record
            if prev_record is not None:
                delta = record.diff_against(prev_record)
                for change in delta.changes:
                    print(f"'{change.field}' changed from '{change.old}' to '{change.new}' from {change.changed_by}")

        context["results"] = self.request.GET.get("model")
        return context
