from django.contrib import admin

from coldfront.plugins.advanced_search.models import SavedSearch


@admin.register(SavedSearch)
class SavedSearchAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "created", "modified")
    list_filter = ("owner",)
    search_fields = ("name", "description", "owner__username")
    filter_horizontal = ("shared_with_users", "shared_with_groups")
