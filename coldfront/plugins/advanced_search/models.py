import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import models
from django.db.models import Q
from model_utils.models import TimeStampedModel
from simple_history.models import HistoricalRecords

User = get_user_model()


class SavedSearch(TimeStampedModel):
    name = models.CharField(max_length=255)
    description = models.TextField()
    query_data = models.JSONField(help_text="JSON representation of the search query parameters")
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="saved_searches")
    shared_with_users = models.ManyToManyField(User, blank=True, related_name="shared_searches")
    shared_with_groups = models.ManyToManyField(Group, blank=True, related_name="shared_searches")
    history = HistoricalRecords()

    class Meta:
        verbose_name = "Saved Search"
        verbose_name_plural = "Saved Searches"
        ordering = ["-modified"]

    def __str__(self):
        return f"{self.name} (by {self.owner})"

    @classmethod
    def get_for_user(cls, user):
        """Return all saved searches owned by the given user."""
        return cls.objects.filter(owner=user)

    @classmethod
    def get_shared_with_user(cls, user):
        """Return saved searches shared with the user (not owned by them)."""
        return (
            cls.objects.filter(Q(shared_with_users=user) | Q(shared_with_groups__in=user.groups.all()))
            .exclude(owner=user)
            .distinct()
        )

    @staticmethod
    def format_query_data(value):
        """Format JSON query data for display."""
        if value is None:
            return "{}"
        if isinstance(value, dict):
            return json.dumps(value, indent=2, sort_keys=True)
        if isinstance(value, str):
            try:
                data = json.loads(value)
                return json.dumps(data, indent=2, sort_keys=True)
            except json.JSONDecodeError:
                return value
        return str(value)
