# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from django.core.management.base import BaseCommand

from coldfront.core.allocation.models import (
    AllocationAttributeType,
    AllocationChangeStatusChoice,
    AllocationStatusChoice,
    AllocationUserRequestStatusChoice,
    AllocationUserRoleChoice,
    AllocationUserStatusChoice,
    AttributeType,
)


class Command(BaseCommand):
    help = "Add default allocation related choices"

    def handle(self, *args, **options):
        for attribute_type in ("Date", "Float", "Int", "Text", "Yes/No", "No", "Attribute Expanded Text", "True/False"):
            AttributeType.objects.get_or_create(name=attribute_type)

        for choice in (
            "Active",
            "Approved",
            "Denied",
            "Expired",
            "New",
            "Paid",
            "Payment Pending",
            "Payment Requested",
            "Payment Declined",
            "Pending",
            "Renewal Requested",
            "Revoked",
            "Unpaid",
        ):
            AllocationStatusChoice.objects.get_or_create(name=choice)

        for choice in (
            "Pending",
            "Approved",
            "Denied",
        ):
            AllocationChangeStatusChoice.objects.get_or_create(name=choice)

        for choice in (
            "Active",
            "Error",
            "Removed",
            "PendingEULA",
            "DeclinedEULA",
            "Invited",
            "Pending",
            "Disabled",
            "Retired",
        ):
            AllocationUserStatusChoice.objects.get_or_create(name=choice)

        for choice in (
            "Approved",
            "Pending",
            "Denied",
        ):
            AllocationUserRequestStatusChoice.objects.get_or_create(name=choice)

        for choice, is_user_default, is_manager_default in (("read/write", True, True), ("read only", False, False)):
            AllocationUserRoleChoice.objects.get_or_create(
                name=choice, is_user_default=is_user_default, is_manager_default=is_manager_default
            )

        for name, attribute_type, has_usage, is_private, is_required, is_changeable in (
            ("Cloud Account Name", "Text", False, False, False, False),
            ("CLOUD_USAGE_NOTIFICATION", "Yes/No", False, True, False, False),
            ("Core Usage (Hours)", "Int", True, False, False, False),
            ("Accelerator Usage (Hours)", "Int", True, False, False, False),
            ("Cloud Storage Quota (TB)", "Float", True, False, False, False),
            ("EXPIRE NOTIFICATION", "Yes/No", False, True, False, False),
            ("freeipa_group", "Text", False, False, False, False),
            ("Is Course?", "Yes/No", False, True, False, False),
            ("Paid", "Float", False, False, False, False),
            ("Paid Cloud Support (Hours)", "Float", True, True, False, False),
            ("Paid Network Support (Hours)", "Float", True, True, False, False),
            ("Paid Storage Support (Hours)", "Float", True, True, False, False),
            ("Purchase Order Number", "Int", False, True, False, False),
            ("send_expiry_email_on_date", "Date", False, True, False, False),
            ("slurm_account_name", "Text", False, False, False, False),
            ("slurm_parent", "Text", False, False, False, False),
            ("slurm_specs", "Attribute Expanded Text", False, True, False, False),
            ("slurm_specs_attriblist", "Text", False, True, False, False),
            ("slurm_user_specs", "Attribute Expanded Text", False, True, False, False),
            ("slurm_user_specs_attriblist", "Text", False, True, False, False),
            ("Storage Quota (GB)", "Int", False, False, False, False),
            ("Storage_Group_Name", "Text", False, False, False, False),
            ("SupportersQOS", "Yes/No", False, False, False, False),
            ("SupportersQOSExpireDate", "Date", False, False, False, False),
            ("Account Number", "Text", False, False, False, False),
            ("Use Type", "Text", False, True, False, False),
            ("Will Exceed Limits", "Yes/No", False, True, False, False),
            ("Allocated Quantity", "Int", False, False, False, False),
            ("Center Identifier", "Text", False, True, False, False),
            ("GID", "Int", False, True, False, False),
            ("LDAP Group", "Text", False, True, False, False),
            ("SMB Enabled", "Yes/No", False, False, False, False),
            ("Slate-Project Directory", "Text", False, False, False, False),
        ):
            AllocationAttributeType.objects.get_or_create(
                name=name,
                attribute_type=AttributeType.objects.get(name=attribute_type),
                has_usage=has_usage,
                is_private=is_private,
                is_required=is_required,
                is_changeable=is_changeable,
            )
