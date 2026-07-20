from django.urls import path

from coldfront.plugins.advanced_search.views import (
    AdvancedExportView,
    AdvancedSearchView,
    ApplySavedSearchView,
    ClearSearchView,
    LoadSavedSearchView,
    SavedSearchCopyView,
    SavedSearchCreateView,
    SavedSearchDeleteView,
    SavedSearchDetailView,
    SavedSearchListView,
    SavedSearchModifyView,
    SharedSearchListView,
)

urlpatterns = [
    path("advanced-search/", AdvancedSearchView.as_view(), name="advanced-search"),
    path("export/", AdvancedExportView.as_view(), name="export"),
    path("save-search/", SavedSearchCreateView.as_view(), name="save-search"),
    path("saved-searches/", SavedSearchListView.as_view(), name="saved-searches"),
    path("shared-searches/", SharedSearchListView.as_view(), name="shared-searches"),
    path("apply-saved-search/<int:pk>/", ApplySavedSearchView.as_view(), name="apply-saved-search"),
    path("modify-search/<int:pk>/", SavedSearchModifyView.as_view(), name="modify-search"),
    path("delete-search/<int:pk>/", SavedSearchDeleteView.as_view(), name="delete-search"),
    path("search-details/<int:pk>/", SavedSearchDetailView.as_view(), name="search-details"),
    path("save-search-form-body/", SavedSearchCreateView.as_view(), name="save-search-form-body"),
    path("load-saved-search/<int:pk>/", LoadSavedSearchView.as_view(), name="load-saved-search"),
    path("clear-search/", ClearSearchView.as_view(), name="clear-search"),
    path("copy-search/<int:pk>/", SavedSearchCopyView.as_view(), name="copy-search"),
]
