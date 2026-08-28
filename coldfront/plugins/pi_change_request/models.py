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

    name = models.CharField(max_length=64, unique=True)
    objects = ProjectPiChangeRequestStatusChoiceManager()

    def __str__(self):
        return self.name

    def natural_key(self):
        return (self.name,)


class ProjectPiChangeRequest(TimeStampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    current_pi = models.ForeignKey(User, on_delete=models.CASCADE, related_name="current_pi")
    new_pi = models.ForeignKey(User, on_delete=models.CASCADE, related_name="new_pi")
    initiator = models.ForeignKey(User, on_delete=models.CASCADE, related_name="initiator")
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

    def create_user_approvals(self, users):
        pending_status = ProjectPiChangeRequestUserApprovalStatusChoice.objects.get(name="Pending")
        for user in users:
            ProjectPiChangeRequestUserApproval.objects.get_or_create(
                request=self, user=user, defaults={"status": pending_status}
            )

    def update_status_from_approvals(self):
        """Recompute this request's status from its user and resource approvals.

        Denied anywhere -> Blocked; all Approved -> Ready; otherwise Awaiting
        Approvals. Terminal states (Complete/Rejected) are left untouched.
        """
        if self.status.name in ["Complete", "Rejected"]:
            return

        approvals = list(self.user_approvals.select_related("status")) + list(
            self.resource_approvals.select_related("status")
        )
        if not approvals:
            return

        statuses = [approval.status.name for approval in approvals]
        if "Denied" in statuses:
            new_status_name = "Blocked"
        elif all(name == "Approved" for name in statuses):
            new_status_name = "Ready"
        else:
            new_status_name = "Awaiting Approvals"

        if self.status.name != new_status_name:
            self.status = ProjectPiChangeRequestStatusChoice.objects.get_by_natural_key(new_status_name)
            self.save()

    def __str__(self):
        return f"{self.project.title} ({self.current_pi} -> {self.new_pi})"


class ProjectPiChangeRequestResourceApprovalStatusChoice(TimeStampedModel):
    class Meta:
        ordering = ["name"]

    class ProjectPiChangeRequestResourceApprovalStatusChoiceManager(models.Manager):
        def get_by_natural_key(self, name):
            return self.get(name=name)

    name = models.CharField(max_length=64, unique=True)
    objects = ProjectPiChangeRequestResourceApprovalStatusChoiceManager()

    def __str__(self):
        return self.name

    def natural_key(self):
        return (self.name,)


class ProjectPiChangeRequestResourceApproval(TimeStampedModel):
    request = models.ForeignKey(ProjectPiChangeRequest, on_delete=models.CASCADE, related_name="resource_approvals")
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

    name = models.CharField(max_length=64, unique=True)
    objects = ProjectPiChangeRequestUserApprovalStatusChoiceManager()

    def __str__(self):
        return self.name

    def natural_key(self):
        return (self.name,)


class ProjectPiChangeRequestUserApproval(TimeStampedModel):
    request = models.ForeignKey(ProjectPiChangeRequest, on_delete=models.CASCADE, related_name="user_approvals")
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    status = models.ForeignKey(ProjectPiChangeRequestUserApprovalStatusChoice, on_delete=models.CASCADE)
    history = HistoricalRecords()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["request", "user"], name="unique_user_approval_per_request"),
        ]
