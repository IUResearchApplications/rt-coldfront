from django.contrib.auth.mixins import UserPassesTestMixin

from coldfront.plugins.advanced_search.models import SavedSearch


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

    The fetched SavedSearch is cached on self.saved_search so that the
    view does not need to query it again.
    """

    def test_func(self):
        pk = self.kwargs.get("pk")
        if not hasattr(self, "saved_search"):
            self.saved_search = SavedSearch.objects.filter(pk=pk).first()

        if not self.saved_search:
            return False

        if super().test_func():
            return True

        if (
            self.saved_search.owner == self.request.user
            or self.saved_search.shared_with_users.filter(pk=self.request.user.pk).exists()
            or self.saved_search.shared_with_groups.filter(
                pk__in=self.request.user.groups.values_list("pk", flat=True)
            ).exists()
        ):
            return True

        return False
