from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import models
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
