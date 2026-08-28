from django.urls import path

from coldfront.plugins.pi_change_request.views import (
    ProjectPiChangeApprovalView,
    ProjectPiChangeDenialView,
    ProjectPiChangeDetailView,
    ProjectPiChangeRequestCenterView,
    ProjectPiChangeRequestResourceApprovalSettingView,
    ProjectPiChangeRequestUserApprovalView,
    ProjectPiChangeRequestUserApprovedView,
    ProjectPiChangeRequestUserDeniedView,
    ProjectPiChangeRequestView,
)

urlpatterns = [
    path("<int:pk>", ProjectPiChangeRequestView.as_view(), name="pi-change-request"),
    path("<int:pk>/approval", ProjectPiChangeApprovalView.as_view(), name="pi-change-request-approval"),
    path("<int:pk>/denial", ProjectPiChangeDenialView.as_view(), name="pi-change-request-denial"),
    path("<int:pk>/detail", ProjectPiChangeDetailView.as_view(), name="pi-change-request-details"),
    path("center", ProjectPiChangeRequestCenterView.as_view(), name="pi-change-request-center"),
    path(
        "center/update-resource-approval",
        ProjectPiChangeRequestResourceApprovalSettingView.as_view(),
        name="update-resource-approval",
    ),
    path("<int:pk>/user/detail", ProjectPiChangeRequestUserApprovalView.as_view(), name="pi-change-request-user"),
    path(
        "<int:pk>/user/approve", ProjectPiChangeRequestUserApprovedView.as_view(), name="pi-change-request-user-approve"
    ),
    path("<int:pk>/user/deny", ProjectPiChangeRequestUserDeniedView.as_view(), name="pi-change-request-user-deny"),
]
