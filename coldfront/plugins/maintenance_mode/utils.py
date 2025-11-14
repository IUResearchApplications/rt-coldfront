import logging

from coldfront.plugins.maintenance_mode.models import Maintenance

logger = logging.getLogger(__name__)


def get_maintenance_mode_status():
    return Maintenance.objects.filter(is_active=True).exists()


def set_maintenance_mode_status(pk, status):
    maintenance_obj = Maintenance.objects.get(pk=pk)
    maintenance_obj.is_active = status
    maintenance_obj.save()

    logger.info(f"Maintenance mode has been set to {status}")
