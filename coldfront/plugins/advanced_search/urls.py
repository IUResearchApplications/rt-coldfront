from django.urls import path

from coldfront.plugins.advanced_search.views import AdvancedExportView, AdvancedSearchView

urlpatterns = [
    path("advanced-search/", AdvancedSearchView.as_view(), name="advanced-search"),
    path("export/", AdvancedExportView.as_view(), name="export"),
]
