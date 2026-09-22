from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.db import models
from model_utils.models import TimeStampedModel
from simple_history.models import HistoricalRecords

from coldfront.core.project.models import Project, ProjectUser, ProjectUserRoleChoice, ProjectUserStatusChoice
from coldfront.core.resource.models import Resource

ACTIVE_REQUEST_STATUSES = ["New", "Awaiting Approvals", "Blocked", "Ready"]
PI_CHANGE_DISALLOWED_PROJECT_STATUSES = ["Archived", "Denied", "Expired", "Renewal Denied"]


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
        if not (self.project_id and self.new_pi_id):
            return

        if self.project.status.name in PI_CHANGE_DISALLOWED_PROJECT_STATUSES:
            raise ValidationError(
                f"A PI change request is not allowed for a project with status {self.project.status.name}."
            )

        if self.new_pi_id == self.project.pi_id:
            raise ValidationError("The new PI must be different from the current PI.")

        project_manager = self.project.projectuser_set.filter(
            user=self.new_pi, status__name="Active", role__name="Manager"
        ).first()
        if not project_manager:
            raise ValidationError("The new PI must be a manager in the project.")

        if (
            ProjectPiChangeRequest.objects.filter(project=self.project, status__name__in=ACTIVE_REQUEST_STATUSES)
            .exclude(pk=self.pk)
            .exists()
        ):
            raise ValidationError("An active PI change request already exists for this project.")

    def create_resource_approvals(self):
        settings = ProjectPiChangeRequestResourceApprovalSetting.objects.filter(
            resource__in=self.resources.all(), requires_approval=True
        ).select_related("resource")
        approvals = []
        for setting in settings:
            approvals.append(
                ProjectPiChangeRequestResourceApproval.objects.create(
                    request=self,
                    resource=setting.resource,
                    status=ProjectPiChangeRequestResourceApprovalStatusChoice.objects.get_by_natural_key("Pending"),
                )
            )
        return approvals

    def create_user_approvals(self, users):
        pending_status = ProjectPiChangeRequestUserApprovalStatusChoice.objects.get_by_natural_key("Pending")
        approvals = []
        for user in users:
            approval, _ = ProjectPiChangeRequestUserApproval.objects.get_or_create(
                request=self, user=user, defaults={"status": pending_status}
            )
            approvals.append(approval)
        return approvals

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

    def cancel_pending_approvals(self):
        """Mark any remaining pending approvals as cancelled after the request reaches a terminal state."""
        user_cancelled_status = ProjectPiChangeRequestUserApprovalStatusChoice.objects.get_by_natural_key("Cancelled")
        for approval in self.user_approvals.filter(status__name="Pending"):
            approval.status = user_cancelled_status
            approval.save()

        resource_cancelled_status = ProjectPiChangeRequestResourceApprovalStatusChoice.objects.get_by_natural_key(
            "Cancelled"
        )
        for approval in self.resource_approvals.filter(status__name="Pending"):
            approval.status = resource_cancelled_status
            approval.save()

    def apply_pi_change(self):
        """Switch the project's PI to the new PI.

        The outgoing PI is kept on the project as an active manager.
        """
        self.project.pi = self.new_pi
        self.project.save()

        manager_role = ProjectUserRoleChoice.objects.get(name="Manager")
        active_status = ProjectUserStatusChoice.objects.get(name="Active")
        ProjectUser.objects.update_or_create(
            project=self.project, user=self.current_pi, defaults={"role": manager_role, "status": active_status}
        )

    @property
    def is_new_pi_active_manager(self):
        """The change is only valid while the new PI remains an active manager on the project."""
        return self.project.projectuser_set.filter(
            user=self.new_pi, status__name="Active", role__name="Manager"
        ).exists()

    @property
    def is_ready(self):
        return self.status.name == "Ready"

    @property
    def is_denyable(self):
        """Admins may deny a request in any active state."""
        return self.status.name in ACTIVE_REQUEST_STATUSES

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

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["request", "resource"], name="unique_resource_approval_per_request"),
        ]

    def clean(self):
        super().clean()
        if not (self.request_id and self.resource_id):
            return

        if not self.request.resources.filter(pk=self.resource.pk).exists():
            raise ValidationError(f"Resource {self.resource} is not associated with this PI Change Request.")

    def __str__(self):
        return f"Resource approval for {self.resource} ({self.status})"


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

    def __str__(self):
        return f"User approval for {self.user} ({self.status})"


class ProjectPiChangeRequestReviewGroupTicketEmail(TimeStampedModel):
    """Maps a review group to the ticket queue email notified about pending resource approvals."""

    group = models.OneToOneField(Group, on_delete=models.CASCADE)
    email = models.EmailField()

    def __str__(self):
        return f"{self.group.name} ({self.email})"
