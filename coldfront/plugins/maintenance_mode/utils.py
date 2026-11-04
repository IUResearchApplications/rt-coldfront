import logging
import pathlib

from django.conf import settings

logger = logging.getLogger(__name__)

MAINTENANCE_MODE_FILE_NAME = "maintenance_mode.txt"


def _maintenance_file_path():
    return pathlib.Path(settings.MAINTENANCE_MODE_BASE_DIR).expanduser() / MAINTENANCE_MODE_FILE_NAME


def get_maintenance_mode_status():
    maintenance_file = _maintenance_file_path()
    try:
        with maintenance_file.open() as m_file:
            return bool(int(m_file.readline()))
    except FileNotFoundError:
        return False
    except (ValueError, OSError):
        logger.warning(
            "Could not read maintenance mode status from %s, assuming off",
            maintenance_file,
        )
        return False


def set_maintenance_mode_status(status):
    maintenance_file = _maintenance_file_path()
    maintenance_file.parent.mkdir(parents=True, exist_ok=True)
    with maintenance_file.open("w") as m_file:
        m_file.write(str(int(status)))

    logger.info(f"Maintenance mode has been set to {status}")
