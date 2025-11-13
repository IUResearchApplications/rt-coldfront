import datetime
import uuid

from django.db import models
from django.db.models import Q
from django.forms import ValidationError
from model_utils.models import TimeStampedModel
from simple_history.models import HistoricalRecords


class MaintenanceTypeChoice(TimeStampedModel):
    class Meta:
        ordering = [
            "name",
        ]

    class MaintenanceTypeChoiceManager(models.Manager):
        def get_by_natural_key(self, name):
            return self.get(name=name)

    name = models.CharField(max_length=64)
    objects = MaintenanceTypeChoiceManager()

    def __str__(self):
        return self.name

    def natural_key(self):
        return (self.name,)


class Maintenance(TimeStampedModel):
    class Meta:
        ordering = [
            "start_date_time",
        ]

    uuid = models.UUIDField(default=uuid.uuid4)
    title = models.CharField(max_length=512)
    prior_message = models.CharField(max_length=512, verbose_name="upcoming maintenance message")
    during_message = models.CharField(max_length=512, verbose_name="during maintenance message")
    start_date_time = models.DateTimeField(verbose_name="maintenance start time")
    end_date_time = models.DateTimeField(verbose_name="maintenance end time")
    notification_days = models.IntegerField(verbose_name="days to notify users before maintenance")
    type = models.ForeignKey(MaintenanceTypeChoice, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=False)
    history = HistoricalRecords()

    def clean(self):
        if self.end_date_time < datetime.datetime.now(tz=datetime.timezone.utc):
            raise ValidationError("End date cannot be less than now.")

        if self.start_date_time >= self.end_date_time:
            raise ValidationError("Start date cannot be greater than or equal to end date.")

        query = Maintenance.objects.filter(
            Q(start_date_time__gte=self.start_date_time, end_date_time__gte=self.start_date_time)
            | Q(start_date_time__lte=self.start_date_time, end_date_time__gte=self.start_date_time)
        )
        if query.exists():
            raise ValidationError("Cannot schedule maintenance to run during an existing one.")
