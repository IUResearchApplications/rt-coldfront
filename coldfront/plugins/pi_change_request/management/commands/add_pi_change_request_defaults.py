from django.core.management.base import BaseCommand

from coldfront.core.resource.models import Resource
from coldfront.plugins.pi_change_request.models import ProjectPiChangeRequestResourceApprovalSetting


class Command(BaseCommand):
    help = "Add PI change request plugin defaults"

    def handle(self, *args, **options):
        resources = Resource.objects.filter(is_allocatable=True)
        for resource in resources:
            ProjectPiChangeRequestResourceApprovalSetting.objects.get_or_create(
                resource=resource, requires_approval=False
            )
