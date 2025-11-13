from django.urls import path

from coldfront.plugins.maintenance_mode.views import (
    MaintenanceCreateView,
    MaintenanceListView,
    MaintenanceUpdateView,
)

urlpatterns = [
    path("maintenance-create", MaintenanceCreateView.as_view(), name="maintenance-create"),
    path("maintenance-list", MaintenanceListView.as_view(), name="maintenance-list"),
    path("<int:pk>/maintenance-update", MaintenanceUpdateView.as_view(), name="maintenance-update"),
]
