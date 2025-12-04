from django.urls import path

from coldfront.plugins.request_forms.views import SoftwareRequestView, StatsRequestView

app_name = "request_forms"
urlpatterns = [
    path("software-request", SoftwareRequestView.as_view(), name="software-request"),
    path("stats-request", StatsRequestView.as_view(), name="stats-request"),
]
