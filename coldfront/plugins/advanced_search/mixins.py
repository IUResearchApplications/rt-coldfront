from django.contrib.auth.mixins import UserPassesTestMixin

from coldfront.plugins.advanced_search.utils import check_saved_search_access


class AdvancedSearchPermissionMixin(UserPassesTestMixin):
    """Mixin that grants access to superusers or users with global view perms."""

    def test_func(self):
        user = self.request.user
        if user.is_superuser:
            return True

        return user.has_perms(["project.can_view_all_projects", "allocation.can_view_all_allocations"])


class CanAccessSavedSearchMixin(AdvancedSearchPermissionMixin):
    """
    Mixin that restricts access to saved search owners or shared users.

    Checks superuser/global-perm first via AdvancedSearchPermissionMixin,
    then falls back to an ownership/share check.
    """

    def test_func(self):
        if super().test_func():
            return True

        if check_saved_search_access(self.kwargs.get("pk"), self.request.user):
            return True

        return False
