from django.urls import path

from coldfront.plugins.history_report.views import HistoryReportResultsView, HistoryReportView

urlpatterns = [
    path("", HistoryReportView.as_view(), name="history-report"),
    path("results", HistoryReportResultsView.as_view(), name="history-report-results"),
]
