import django.dispatch
from django.db.models.signals import post_save
from django.dispatch import receiver

from coldfront.core.resource.models import Resource
from coldfront.plugins.pi_change_request.models import ProjectPiChangeRequestResourceApprovalSetting

# providing_args=["pi_change_request_pk"]
pi_change_request_created = django.dispatch.Signal()

# providing_args=["user_approval_pk", "pi_change_request_pk"]
pi_change_request_user_response = django.dispatch.Signal()

# providing_args=["resource_approval_pk", "pi_change_request_pk"]
pi_change_request_resource_response = django.dispatch.Signal()

# providing_args=["pi_change_request_pk"]
pi_change_request_completed = django.dispatch.Signal()


@receiver(post_save, sender=Resource)
def create_pi_change_request_setting(sender, instance, **kwargs):
    if instance.is_allocatable:
        ProjectPiChangeRequestResourceApprovalSetting.objects.get_or_create(
            resource=instance, defaults={"requires_approval": False}
        )
