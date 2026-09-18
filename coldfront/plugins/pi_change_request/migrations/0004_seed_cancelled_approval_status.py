from django.db import migrations


def seed_cancelled_approval_status(apps, schema_editor):
    ResourceApprovalStatus = apps.get_model("pi_change_request", "ProjectPiChangeRequestResourceApprovalStatusChoice")
    UserApprovalStatus = apps.get_model("pi_change_request", "ProjectPiChangeRequestUserApprovalStatusChoice")
    ResourceApprovalStatus.objects.get_or_create(name="Cancelled")
    UserApprovalStatus.objects.get_or_create(name="Cancelled")


def remove_cancelled_approval_status(apps, schema_editor):
    ResourceApprovalStatus = apps.get_model("pi_change_request", "ProjectPiChangeRequestResourceApprovalStatusChoice")
    UserApprovalStatus = apps.get_model("pi_change_request", "ProjectPiChangeRequestUserApprovalStatusChoice")
    ResourceApprovalStatus.objects.filter(name="Cancelled").delete()
    UserApprovalStatus.objects.filter(name="Cancelled").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("pi_change_request", "0003_projectpichangerequestreviewgroupticketemail"),
    ]

    operations = [
        migrations.RunPython(seed_cancelled_approval_status, remove_cancelled_approval_status),
    ]
