from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from model_utils.models import TimeStampedModel
from simple_history.models import HistoricalRecords

from coldfront.core.project.models import Project
from coldfront.core.resource.models import Resource


class ProjectPiChangeRequestStatusChoice(TimeStampedModel):
    class Meta:
        ordering = ["name"]

    class ProjectPiChangeRequestStatusChoiceManager(models.Manager):
        def get_by_natural_key(self, name):
            return self.get(name=name)

    name = models.CharField(max_length=64)
    objects = ProjectPiChangeRequestStatusChoiceManager()

    def __str__(self):
        return self.name

    def natural_key(self):
        return (self.name,)


class ProjectPiChangeRequest(TimeStampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    new_pi = models.ForeignKey(User, on_delete=models.CASCADE)
    justification = models.TextField()
    status = models.ForeignKey(ProjectPiChangeRequestStatusChoice, on_delete=models.CASCADE)
    resources = models.ManyToManyField(Resource)
    history = HistoricalRecords()

    def clean(self):
        super().clean()
        project_manager = self.project.projectuser_set.filter(
            user=self.new_pi, status__name="Active", role__name="Manager"
        ).first()
        if not project_manager:
            raise ValidationError("The new PI must be a manager in the project.")

    def create_change_request_group_approvals(self):
        settings = ProjectPiChangeRequestRequiresApprovalSetting.objects.filter(
            resource__in=self.resources.all(), requires_approval=True
        ).select_related("resource")
        for setting in settings:
            ProjectPiChangeRequestApproval.objects.create(
                request=self,
                resource=setting.resource,
                status=ProjectPiChangeRequestApprovalStatusChoice.objects.get_by_natural_key("Pending"),
            )


class ProjectPiChangeRequestApprovalStatusChoice(TimeStampedModel):
    class Meta:
        ordering = ["name"]

    class ProjectPiChangeRequestApprovalStatusChoiceManager(models.Manager):
        def get_by_natural_key(self, name):
            return self.get(name=name)

    name = models.CharField(max_length=64)
    objects = ProjectPiChangeRequestApprovalStatusChoiceManager()

    def __str__(self):
        return self.name

    def natural_key(self):
        return (self.name,)


class ProjectPiChangeRequestApproval(TimeStampedModel):
    request = models.ForeignKey(ProjectPiChangeRequest, on_delete=models.CASCADE)
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE)
    status = models.ForeignKey(ProjectPiChangeRequestApprovalStatusChoice, on_delete=models.CASCADE)
    handler = models.ForeignKey(User, on_delete=models.CASCADE, blank=True, null=True)
    history = HistoricalRecords()

    def clean(self):
        super().clean()
        if not self.request.resources.filter(pk=self.resource.pk).exists():
            raise ValidationError(f"Resource {self.resource} is not associated with this PI Change Request.")


class ProjectPiChangeRequestRequiresApprovalSetting(TimeStampedModel):
    resource = models.OneToOneField(Resource, on_delete=models.CASCADE)
    requires_approval = models.BooleanField()
    history = HistoricalRecords()
