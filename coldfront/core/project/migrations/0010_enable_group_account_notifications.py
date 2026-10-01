from django.db import migrations


def enable_group_account_notifications(apps, schema_editor):
    """Enable notifications for existing group account project users.

    Group accounts were previously added with notifications disabled. They are now
    enabled by default so group accounts receive allocation emails (e.g. when a
    storage allocation is revoked).
    """
    ProjectUser = apps.get_model("project", "ProjectUser")
    ProjectUser.objects.filter(role__name="Group").update(enable_notifications=True)


def disable_group_account_notifications(apps, schema_editor):
    """Restore the previous default of disabled notifications for group account project users."""
    ProjectUser = apps.get_model("project", "ProjectUser")
    ProjectUser.objects.filter(role__name="Group").update(enable_notifications=False)


class Migration(migrations.Migration):
    dependencies = [
        ("project", "0009_remove_historicalproject_slurm_account_name_and_more"),
    ]

    operations = [
        migrations.RunPython(enable_group_account_notifications, disable_group_account_notifications),
    ]
