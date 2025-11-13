from django.core.management.base import BaseCommand

from coldfront.core.resource.models import Resource
from coldfront.plugins.maintenance_mode.models import MaintenanceTypeChoice


class Command(BaseCommand):
    help = "Add maintenance types from available resources"

    def handle(self, *args, **options):
        MaintenanceTypeChoice.objects.get_or_create(name="Site")
        for choice in Resource.objects.filter(is_allocatable=True).values_list("name", flat=True):
            MaintenanceTypeChoice.objects.get_or_create(name=choice)
