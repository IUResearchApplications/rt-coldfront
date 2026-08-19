from django.db.models.signals import post_save
from django.dispatch import receiver

from coldfront.core.resource.models import Resource
from coldfront.plugins.pi_change_request.models import ProjectPiChangeRequestRequiresApprovalSetting


@receiver(post_save, sender=Resource)
def create_pi_change_request_setting(sender, instance, created, **kwargs):
    if created and instance.is_allocatable:
        ProjectPiChangeRequestRequiresApprovalSetting.objects.get_or_create(resource=instance, requires_approval=False)
