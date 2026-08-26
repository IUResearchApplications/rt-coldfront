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
    current_pi = models.ForeignKey(User, on_delete=models.CASCADE, related_name="current_pi")
    new_pi = models.ForeignKey(User, on_delete=models.CASCADE, related_name="new_pi")
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

    def create_resource_approvals(self):
        settings = ProjectPiChangeRequestResourceApprovalSetting.objects.filter(
            resource__in=self.resources.all(), requires_approval=True
        ).select_related("resource")
        for setting in settings:
            ProjectPiChangeRequestResourceApproval.objects.create(
                request=self,
                resource=setting.resource,
                status=ProjectPiChangeRequestResourceApprovalStatusChoice.objects.get_by_natural_key("Pending"),
            )


class ProjectPiChangeRequestResourceApprovalStatusChoice(TimeStampedModel):
    class Meta:
        ordering = ["name"]

    class ProjectPiChangeRequestResourceApprovalStatusChoiceManager(models.Manager):
        def get_by_natural_key(self, name):
            return self.get(name=name)

    name = models.CharField(max_length=64)
    objects = ProjectPiChangeRequestResourceApprovalStatusChoiceManager()

    def __str__(self):
        return self.name

    def natural_key(self):
        return (self.name,)


class ProjectPiChangeRequestResourceApproval(TimeStampedModel):
    request = models.ForeignKey(ProjectPiChangeRequest, on_delete=models.CASCADE)
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE)
    status = models.ForeignKey(ProjectPiChangeRequestResourceApprovalStatusChoice, on_delete=models.CASCADE)
    handler = models.ForeignKey(User, on_delete=models.CASCADE, blank=True, null=True)
    history = HistoricalRecords()

    def clean(self):
        super().clean()
        if not self.request.resources.filter(pk=self.resource.pk).exists():
            raise ValidationError(f"Resource {self.resource} is not associated with this PI Change Request.")


class ProjectPiChangeRequestResourceApprovalSetting(TimeStampedModel):
    resource = models.OneToOneField(Resource, on_delete=models.CASCADE)
    requires_approval = models.BooleanField()
    history = HistoricalRecords()


class ProjectPiChangeRequestUserApprovalStatusChoice(TimeStampedModel):
    class Meta:
        ordering = ["name"]

    class ProjectPiChangeRequestUserApprovalStatusChoiceManager(models.Manager):
        def get_by_natural_key(self, name):
            return self.get(name=name)

    name = models.CharField(max_length=64)
    objects = ProjectPiChangeRequestUserApprovalStatusChoiceManager()

    def __str__(self):
        return self.name

    def natural_key(self):
        return (self.name,)


class ProjectPiChangeRequestUserApproval(TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    status = models.ForeignKey(ProjectPiChangeRequestUserApprovalStatusChoice, on_delete=models.CASCADE)
