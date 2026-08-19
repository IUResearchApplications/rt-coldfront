from django.urls import path

from coldfront.plugins.pi_change_request.views import ProjectPiChangeRequestCenterView, ProjectPiChangeRequestView

urlpatterns = [
    path("<int:pk>", ProjectPiChangeRequestView.as_view(), name="pi-change-request"),
    path("center", ProjectPiChangeRequestCenterView.as_view(), name="pi-change-request-center"),
]
