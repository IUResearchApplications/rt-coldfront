# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import datetime
import logging
from enum import Enum

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils.html import escape, format_html
from django.utils.module_loading import import_string
from django.utils.safestring import SafeString
from model_utils.models import TimeStampedModel
from simple_history.models import HistoricalRecords

import coldfront.core.attribute_expansion as attribute_expansion
from coldfront.config.core import ALLOCATION_EULA_ENABLE
from coldfront.core.allocation.signals import allocation_activate_user, allocation_expire, allocation_remove_user
from coldfront.core.project.models import Project, ProjectPermission
from coldfront.core.resource.models import Resource, ResourceAttribute, ResourceAttributeType
from coldfront.core.utils.common import get_users_info, import_from_settings
from coldfront.core.utils.groups import check_if_groups_in_review_groups
from coldfront.core.utils.mail import build_link, send_email_template
from coldfront.core.utils.validate import AttributeValidator

if "coldfront.plugins.ldap_misc" in settings.INSTALLED_APPS:
    from coldfront.plugins.ldap_misc.utils.ldap_user_search import get_users_info

logger = logging.getLogger(__name__)

ALLOCATION_ATTRIBUTE_VIEW_LIST = import_from_settings("ALLOCATION_ATTRIBUTE_VIEW_LIST", [])
ALLOCATION_FUNCS_ON_EXPIRE = import_from_settings("ALLOCATION_FUNCS_ON_EXPIRE", [])
ALLOCATION_RESOURCE_ORDERING = import_from_settings("ALLOCATION_RESOURCE_ORDERING", ["-is_allocatable", "name"])
ALLOCATION_DAYS_TO_REVIEW_BEFORE_EXPIRING = import_from_settings("ALLOCATION_DAYS_TO_REVIEW_BEFORE_EXPIRING", 30)
ALLOCATION_DAYS_TO_REVIEW_AFTER_EXPIRING = import_from_settings("ALLOCATION_DAYS_TO_REVIEW_AFTER_EXPIRING", 60)
ALLOCATION_ENABLE_ALLOCATION_RENEWAL = import_from_settings("ALLOCATION_ENABLE_ALLOCATION_RENEWAL", True)
ALLOCATION_ATTRIBUTE_IDENTIFIERS = import_from_settings("ALLOCATION_ATTRIBUTE_IDENTIFIERS", [])

EMAIL_ENABLED = import_from_settings("EMAIL_ENABLED", False)
if EMAIL_ENABLED:
    EMAIL_SENDER = import_from_settings("EMAIL_SENDER")
    EMAIL_TICKET_SYSTEM_ADDRESS = import_from_settings("EMAIL_TICKET_SYSTEM_ADDRESS")
    EMAIL_OPT_OUT_INSTRUCTION_URL = import_from_settings("EMAIL_OPT_OUT_INSTRUCTION_URL")
    EMAIL_SIGNATURE = import_from_settings("EMAIL_SIGNATURE")
    EMAIL_CENTER_NAME = import_from_settings("CENTER_NAME")

ADDITIONAL_USER_SEARCH_CLASSES = import_from_settings("ADDITIONAL_USER_SEARCH_CLASSES", [])

EMAIL_SENDER = import_from_settings("EMAIL_SENDER")


# TODO - review file
class AllocationPermission(Enum):
    """An allocation permission stores the user and manager fields of a project."""

    USER = "USER"
    MANAGER = "MANAGER"


class AllocationStatusChoice(TimeStampedModel):
    """A project status choice indicates the status of the project. Examples include Active, Archived, and New.

    Attributes:
        name (str): name of project status choice
    """

    class Meta:
        ordering = [
            "name",
        ]

    class AllocationStatusChoiceManager(models.Manager):
        def get_by_natural_key(self, name):
            return self.get(name=name)

    name = models.CharField(max_length=64)
    objects = AllocationStatusChoiceManager()

    def __str__(self):
        return self.name

    def natural_key(self):
        return (self.name,)


class Allocation(TimeStampedModel):
    """An allocation provides users access to a resource.

    Attributes:
        project (Project): links the project the allocation falls under
        resources (Resource): links resources that this allocation allocates
        status (AllocationStatusChoice): represents the status of the allocation
        quantity (int): indicates the quantity of the resource for the allocation, if applicable
        start_date (Date): indicates the start date of the allocation
        end_date (Date): indicates the end/ expiry date of the allocation
        justification (str): text input from the user containing the justification for why the resource is being allocated
        description (str): description of the allocation
        is_locked (bool): indicates whether or not the allocation is locked
        is_changeable (bool): indicates whether or not the allocation is changeable
    """

    class Meta:
        ordering = [
            "end_date",
        ]

        permissions = (
            ("can_view_all_allocations", "Can view all allocations"),
            ("can_review_allocation_requests", "Can review allocation requests"),
            ("can_manage_invoice", "Can manage invoice"),
        )

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
    )
    resources = models.ManyToManyField(Resource)
    status = models.ForeignKey(AllocationStatusChoice, on_delete=models.CASCADE, verbose_name="Status")
    quantity = models.IntegerField(blank=True, null=True)
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)
    justification = models.TextField()
    description = models.CharField(max_length=512, blank=True, null=True)
    is_locked = models.BooleanField(default=False)
    is_changeable = models.BooleanField(default=False)
    history = HistoricalRecords()

    def clean(self):
        """Validates the allocation and raises errors if the allocation is invalid."""

        if self.status.name == "Expired":
            if not self.end_date:
                raise ValidationError("You have to set the end date.")

            if self.end_date > datetime.datetime.now().date():
                raise ValidationError("End date cannot be greater than today.")

            if self.start_date > self.end_date:
                raise ValidationError("End date cannot be greater than start date.")

        elif self.status.name == "Active":
            if not self.start_date:
                raise ValidationError("You have to set the start date.")

            if not self.end_date:
                raise ValidationError("You have to set the end date.")

            if self.start_date > self.end_date:
                raise ValidationError("Start date cannot be greater than the end date.")

    def save(self, *args, **kwargs):
        """Saves the project."""

        if self.pk:
            old_obj = Allocation.objects.get(pk=self.pk)
            if old_obj.status.name != self.status.name and self.status.name == "Expired":
                for func_string in ALLOCATION_FUNCS_ON_EXPIRE:
                    func_to_run = import_string(func_string)
                    func_to_run(self.pk)

        super().save(*args, **kwargs)

    @property
    def get_identifiers(self):
        """
        Returns:
            dict: the allocation's attribute types and their values
        """

        info = {}
        for attribute in self.allocationattribute_set.all():
            if attribute.allocation_attribute_type.name in ALLOCATION_ATTRIBUTE_IDENTIFIERS:
                info[attribute.allocation_attribute_type.name] = attribute.value

        return info

    @property
    def get_allocation_attributes(self):
        return self.allocationattribute_set.all()

    @property
    def expires_in(self):
        """
        Returns:
            int: the number of days until the allocation expires
        """

        return (self.end_date - datetime.date.today()).days

    @property
    def can_be_renewed(self):
        """
        Returns:
            bool: whether the allocation can be renewed
        """
        if not ALLOCATION_ENABLE_ALLOCATION_RENEWAL:
            return False

        if self.status.name not in ["Active", "Expired"]:
            return False

        if self.project.needs_review or self.project.status.name not in ["Active", "Review Pending"]:
            return False

        if (
            self.status.name == "Active"
            and self.expires_in <= ALLOCATION_DAYS_TO_REVIEW_BEFORE_EXPIRING
            and self.expires_in >= 0
        ):
            return True

        if self.status.name == "Expired" and ALLOCATION_DAYS_TO_REVIEW_AFTER_EXPIRING < 0:
            return True

        if self.status.name == "Expired" and self.expires_in >= -ALLOCATION_DAYS_TO_REVIEW_AFTER_EXPIRING:
            return True

        return False

    @property
    def get_information(self) -> SafeString:
        """
        Returns:
            SafeString: the allocation's attribute type, usage out of total value, and usage out of total value as a percentage
        """

        html_string = escape("")
        for attribute in self.allocationattribute_set.select_related(
            "allocation_attribute_type", "allocationattributeusage"
        ).all():
            if attribute.allocation_attribute_type.name in ALLOCATION_ATTRIBUTE_VIEW_LIST:
                html_substring = format_html("{}: {} <br>", attribute.allocation_attribute_type.name, attribute.value)
                html_string += html_substring

            if hasattr(attribute, "allocationattributeusage"):
                try:
                    percent = (
                        round(float(attribute.allocationattributeusage.value) / float(attribute.value) * 10000) / 100
                    )
                except ValueError:
                    percent = "Invalid Value"
                    logger.error(
                        "Allocation attribute '%s' is not an int but has a usage",
                        attribute.allocation_attribute_type.name,
                    )
                except ZeroDivisionError:
                    percent = 100
                    logger.error(
                        "Allocation attribute '%s' == 0 but has a usage", attribute.allocation_attribute_type.name
                    )

                html_substring = format_html(
                    "{}: {}/{} ({} %) <br>",
                    attribute.allocation_attribute_type.name,
                    attribute.allocationattributeusage.value,
                    attribute.value,
                    percent,
                )
                html_string += html_substring

        return html_string

    @property
    def get_resources_as_string(self):
        """
        Returns:
            str: the resources for the allocation
        """
        resources = getattr(self, "_parent_resources", None)
        if resources is None:
            resources = self.resources.all().order_by(*ALLOCATION_RESOURCE_ORDERING)
        return ", ".join([ele.name for ele in resources])

    @property
    def get_resources_as_list(self):
        """
        Returns:
            list[Resource]: the resources for the allocation
        """

        return [ele for ele in self.resources.all().order_by("-is_allocatable")]

    @property
    def get_parent_resource(self):
        """
        Returns:
            Resource: the parent resource for the allocation
        """
        resources = getattr(self, "_parent_resources", None)
        if resources is None:
            resources = list(self.resources.select_related("resource_type").order_by(*ALLOCATION_RESOURCE_ORDERING))
        if not resources:
            return None
        return resources[0]

    @property
    def get_user_list(self):
        return self.allocationuser_set.filter(
            status__name__in=["Active", "Retired", "Disabled", "Pending", "Invited"]
        ).values_list("user__username", flat=True)

    def get_attribute(self, name, expand=True, typed=True, extra_allocations=[]):
        """
        Params:
            name (str): name of the allocation attribute type
            expand (bool): indicates whether or not to return the expanded value with attributes/parameters for attributes with a base type of 'Attribute Expanded Text'
            typed (bool): indicates whether or not to convert the attribute value to an int/ float/ str based on the base AttributeType name
            extra_allocations (list[Allocation]): allocations which are available to reference in the attribute list in addition to those associated with this AllocationAttribute

        Returns:
            str: the value of the first attribute found for this allocation with the specified name
        """

        attr = self.allocationattribute_set.filter(allocation_attribute_type__name=name).first()
        if attr:
            if expand:
                return attr.expanded_value(extra_allocations=extra_allocations, typed=typed)
            else:
                if typed:
                    return attr.typed_value()
                else:
                    return attr.value
        return None

    def set_usage(self, name, value):
        """
        Params:
            name (str): allocation attribute type whose usage to set
            value (float): value to set usage to
        """

        attr = self.allocationattribute_set.filter(allocation_attribute_type__name=name).first()
        if not attr:
            return

        if not attr.allocation_attribute_type.has_usage:
            return

        if not AllocationAttributeUsage.objects.filter(allocation_attribute=attr).exists():
            usage = AllocationAttributeUsage.objects.create(allocation_attribute=attr)
        else:
            usage = attr.allocationattributeusage

        usage.value = value
        usage.save()

    def get_attribute_list(self, name, expand=True, typed=True, extra_allocations=[]):
        """
        Params:
            name (str): name of the allocation
            expand (bool): indicates whether or not to return the expanded value with attributes/parameters for attributes with a base type of 'Attribute Expanded Text'
            typed (bool): indicates whether or not to convert the attribute value to an int/ float/ str based on the base AttributeType name
            extra_allocations (list[Allocation]): allocations which are available to reference in the attribute list in addition to those associated with this AllocationAttribute

        Returns:
            list: the list of values of the attributes found with specified name
        """

        attr = self.allocationattribute_set.filter(allocation_attribute_type__name=name).all()
        if expand:
            return [a.expanded_value(typed=typed, extra_allocations=extra_allocations) for a in attr]
        else:
            if typed:
                return [a.typed_value() for a in attr]
            else:
                return [a.value for a in attr]

    def get_attribute_set(self, user, permission=None):
        """
        Params:
            user (User): user for whom to return attributes
            permission (str): extra permission to check

        Returns:
            list[AllocationAttribute]: returns the set of attributes the user is allowed to see (if superuser, then all allocation attributes; else, only non-private ones)
        """

        group_exists = check_if_groups_in_review_groups(
            self.get_parent_resource.review_groups.all(), user.groups.all(), permission
        )
        if user.is_superuser or group_exists:
            return self.allocationattribute_set.all().order_by("allocation_attribute_type__name")

        return self.allocationattribute_set.filter(allocation_attribute_type__is_private=False).order_by(
            "allocation_attribute_type__name"
        )

    def get_visible_notes(self, user):
        """
        Return the allocation user notes visible to the given user.

        Superusers and users with the "allocation.view_allocationusernote"
        permission see all notes; others see only non-private notes.
        """
        noteset = self.allocationusernote_set.select_related("author")
        if user.is_superuser or user.has_perm("allocation.view_allocationusernote"):
            return noteset.all()
        return noteset.filter(is_private=False)

    def has_user_in_allocation(self, user):
        """
        Return whether the given user is a non-removed member of this allocation.
        """
        return self.allocationuser_set.filter(
            user=user,
            status__name__in=["Active", "Invited", "Pending", "Disabled", "Retired"],
        ).exists()

    def user_permissions(self, user, permission=None):
        """
        Params:
            user (User): user for whom to return permissions
            permission (str): extra permission to check

        Returns:
            list[AllocationPermission]: list of user permissions for the allocation
        """

        if user.is_superuser:
            return list(AllocationPermission)

        project_perms = self.project.user_permissions(user)

        if ProjectPermission.USER not in project_perms:
            group_exists = check_if_groups_in_review_groups(
                self.get_parent_resource.review_groups.all(), user.groups.all(), permission
            )
            if group_exists:
                return [AllocationPermission.USER, AllocationPermission.MANAGER]
            return []

        if ProjectPermission.PI in project_perms or ProjectPermission.MANAGER in project_perms:
            return [AllocationPermission.USER, AllocationPermission.MANAGER]

        if self.allocationuser_set.filter(
            user=user, status__name__in=["Active", "New", "Invited", "Pending", "PendingEULA"]
        ).exists():
            return [AllocationPermission.USER]

        return []

    def has_perm(self, user, perm, addtl_perm=None):
        """
        Params:
            user (User): user to check permissions for
            perm (AllocationPermission): permission to check for in user's list

        Returns:
            bool: whether or not the user has the specified permission
        """

        perms = self.user_permissions(user, addtl_perm)
        return perm in perms

    def get_user_roles(self):
        return AllocationUserRoleChoice.objects.filter(resources=self.get_parent_resource)

    def __str__(self):
        return "%s (%s)" % (self.get_parent_resource.name, self.project.pi)

    def get_eula(self):
        if self.get_resources_as_list:
            for res in self.get_resources_as_list:
                if res.get_attribute(name="eula"):
                    return res.get_attribute(name="eula")
        else:
            return None

    def add_user(self, user, signal_sender=None, role=None):
        """
        Adds a user to the allocation.

        If EULAs are enabled and this allocation has an associated EULA, marks the user
            as "PendingEULA" and sends the user an email asking them to agree to the EULA.
        Otherwise, marks the user as "Active." Also sends the `allocation_activate_user`
            signal if the allocation status is "Active."

        Params:
            user (User): User to add.
            signal_sender (str): Sender for the `allocation_activate_user` signal.
            role (AllocationUserRoleChoice): Role to assign the user. When None, an
                existing user's role is left unchanged.
        """
        user_status = "Active"

        is_pending_eula = ALLOCATION_EULA_ENABLE and self.get_eula() and not user.userprofile.is_pi
        if is_pending_eula:
            user_status = "PendingEULA"
        user_status_obj = AllocationUserStatusChoice.objects.get(name=user_status)

        defaults = {"status": user_status_obj}
        if role is not None:
            defaults["role"] = role

        allocation_user, _created = self.allocationuser_set.update_or_create(user=user, defaults=defaults)

        if is_pending_eula:
            send_email_template(
                f"Agree to EULA for {self.get_parent_resource.__str__()}",
                "email/allocation_agree_to_eula.txt",
                {
                    "resource": self.get_parent_resource,
                    "url": build_link(reverse("allocation-review-eula", kwargs={"pk": self.pk})),
                },
                [user.email],
            )

        if self.status.name in ["Active", "Renewal Requested"] and allocation_user.status.name in [
            "Active",
            "Invited",
            "Disabled",
            "Retired",
        ]:
            allocation_activate_user.send(sender=signal_sender, allocation_user_pk=allocation_user.pk)

    def remove_user(self, user, signal_sender=None, ignore_user_not_found=True):
        """
        Marks an `AllocationUser` as 'Removed' and sends the `allocation_remove_user` signal.

        Params:
            user (User|AllocationUser): User to remove.
            signal_sender (str): Sender for the `allocation_remove_user` signal.
            ignore_user_not_found (bool): If enabled, logs a warning that the allocation user for
                the provded user couldn't be found and returns. Otherwise, raises `AllocationUser.DoesNotExist`.
        """
        if isinstance(user, AllocationUser):
            allocation_user = user
        elif isinstance(user, get_user_model()):
            try:
                allocation_user = self.allocationuser_set.get(user=user)
            except AllocationUser.DoesNotExist:
                if ignore_user_not_found:
                    logger.warning(
                        f"Cannot remove user={str(user)} for allocation pk={self.pk} - AllocationUser not found."
                    )
                    return
                else:
                    raise
        allocation_user.status = AllocationUserStatusChoice.objects.get(name="Removed")
        allocation_user.save()
        allocation_remove_user.send(sender=signal_sender, allocation_user_pk=allocation_user.pk)

    def expire(self):
        """
        Sets the allocation status to "Expired" and expires all active allocations.
        """
        # TODO: expiry should probably send an email... (but i think send_expiry_emails() would have to get refactored)
        allocation_status_expired = AllocationStatusChoice.objects.get(name="Expired")
        self.status = allocation_status_expired
        self.end_date = datetime.datetime.now()
        self.save()
        allocation_expire.send(sender=self.__class__, allocation_pk=self.pk)

    def get_absolute_url(self):
        return reverse("allocation-detail", kwargs={"pk": self.pk})

    def get_user_emails(self, status_name="Active", ignore_disabled_notifications=False) -> set[str]:
        """Gets a set of user emails for notifications.

        Params:
            status_name (str): The name of the AllocationUserStatus to filter on. Defaults to "Active".
            ignore_disabled_notifications (bool): If True, include project users
                that have enable_notifications off.

        Returns:
            set: A set of user emails for notifications.
        """
        allocation_users = self.allocationuser_set.filter(status__name=status_name)
        if ignore_disabled_notifications:
            user_emails = set(allocation_users.values_list("user__email", flat=True))
            return user_emails

        users = allocation_users.values_list("user", flat=True)
        filter_options = {
            "user__in": users,
            "status__name": "Active",
            "enable_notifications": True,
        }

        project_users = self.project.projectuser_set.filter(**filter_options)
        user_emails = set(project_users.values_list("user__email", flat=True))
        return user_emails


class AllocationAdminNote(TimeStampedModel):
    """An allocation admin note is a note that an admin makes on an allocation.

    Attributes:
        allocation (Allocation): links the allocation to the note
        author (User): represents the User class of the admin who authored the note
        note (str): text input from the user containing the note
    """

    allocation = models.ForeignKey(Allocation, on_delete=models.CASCADE)
    author = models.ForeignKey(User, on_delete=models.CASCADE)
    note = models.TextField()

    def __str__(self):
        return self.note


class AllocationUserNote(TimeStampedModel):
    """An allocation user note is a note that an user makes on an allocation.

    Attributes:
        allocation (Allocation): links the allocation to the note
        author (User): represents the User class of the user who authored the note
        is_private (bool): indicates whether or not the note is private
        note (str): text input from the user containing the note
    """

    allocation = models.ForeignKey(Allocation, on_delete=models.CASCADE)
    author = models.ForeignKey(User, on_delete=models.CASCADE)
    is_private = models.BooleanField(default=True)
    note = models.TextField()

    def __str__(self):
        return self.note


class AttributeType(TimeStampedModel):
    """An attribute type indicates the data type of the attribute. Examples include Date, Float, Int, Text, and Yes/No.

    Attributes:
        name (str): name of attribute data type
    """

    name = models.CharField(max_length=64)

    def __str__(self):
        return self.name

    class Meta:
        ordering = [
            "name",
        ]


class AllocationAttributeType(TimeStampedModel):
    """An allocation attribute type indicates the type of the attribute. Examples include Cloud Account Name and Core Usage (Hours).

    Attributes:
        attribute_type (AttributeType): indicates the data type of the attribute
        name (str): name of allocation attribute type
        has_usage (bool): indicates whether or not the attribute type has usage
        is_required (bool): indicates whether or not the attribute is required
        is_unique (bool): indicates whether or not the value is unique
        is_private (bool): indicates whether or not the attribute type is private
        is_changeable (bool): indicates whether or not the attribute type is changeable
    """

    attribute_type = models.ForeignKey(AttributeType, on_delete=models.CASCADE)
    name = models.CharField(max_length=50)
    linked_resource_attribute_type = models.ForeignKey(
        ResourceAttributeType, on_delete=models.CASCADE, blank=True, null=True
    )
    linked_resources = models.ManyToManyField(Resource, blank=True)
    has_usage = models.BooleanField(default=False)
    is_required = models.BooleanField(default=False)
    is_unique = models.BooleanField(default=False)
    is_private = models.BooleanField(default=True)
    is_changeable = models.BooleanField(default=False)
    history = HistoricalRecords()

    def __str__(self):
        return "%s" % (self.name)

    def get_linked_resources(self):
        return self.linked_resources.all()

    class Meta:
        ordering = [
            "name",
        ]


class AllocationAttribute(TimeStampedModel):
    """An allocation attribute class links an allocation attribute type and an allocation.

    Attributes:
        allocation_attribute_type (AllocationAttributeType): attribute type to link
        allocation (Allocation): allocation to link
        value (str): value of the allocation attribute
    """

    allocation_attribute_type = models.ForeignKey(AllocationAttributeType, on_delete=models.CASCADE)
    allocation = models.ForeignKey(Allocation, on_delete=models.CASCADE)
    value = models.CharField(max_length=128, db_collation="utf8mb4_unicode_ci")
    history = HistoricalRecords()

    def save(self, *args, **kwargs):
        """Saves the allocation attribute."""

        super().save(*args, **kwargs)
        if (
            self.allocation_attribute_type.has_usage
            and not AllocationAttributeUsage.objects.filter(allocation_attribute=self).exists()
        ):
            AllocationAttributeUsage.objects.create(allocation_attribute=self)

    def clean(self):
        """Validates the allocation attribute and raises errors if the allocation attribute is invalid."""

        if (
            self.allocation_attribute_type.is_unique
            and self.allocation.allocationattribute_set.filter(allocation_attribute_type=self.allocation_attribute_type)
            .exclude(id=self.pk)
            .exists()
        ):
            raise ValidationError(
                "'{}' attribute already exists for this allocation.".format(self.allocation_attribute_type)
            )

        expected_value_type = self.allocation_attribute_type.attribute_type.name.strip()

        validator = AttributeValidator(self.value)
        if expected_value_type == "Int":
            validator.validate_int()
        elif expected_value_type == "Float":
            validator.validate_float()
        elif expected_value_type == "Yes/No":
            validator.validate_yes_no()
        elif expected_value_type == "Date":
            validator.validate_date()

        linked_attribute_type_obj = self.allocation_attribute_type.linked_resource_attribute_type
        linked_attribute_obj = ResourceAttribute.objects.filter(
            resource=self.allocation.get_parent_resource,
            resource_attribute_type=linked_attribute_type_obj,
            check_if_username_exists=True,
        )
        if linked_attribute_obj.exists():
            username = get_users_info([self.value]).get(self.value).get(self.value, "")
            if username is not None and not username:
                raise ValidationError(f"{self.allocation_attribute_type.name} does not have a valid username")

    def __str__(self):
        return "%s" % (self.allocation_attribute_type.name)

    def typed_value(self):
        """
        Returns:
            int, float, str: the value of the attribute with proper type and is used for computing expanded_value() (coerced into int or float for attributes with Int or Float types; if it fails or the attribute is of any other type, it is coerced into a str)
        """

        raw_value = self.value
        atype_name = self.allocation_attribute_type.attribute_type.name
        return attribute_expansion.convert_type(value=raw_value, type_name=atype_name)

    def expanded_value(self, extra_allocations=[], typed=True):
        """
        Params:
            typed (bool): indicates whether or not to convert the attribute value to an int/ float/ str based on the base AttributeType name (unrecognized values not converted, so will return str)
            extra_allocations (list[Allocation]): allocations which are available to reference in the attribute list in addition to those associated with this ResourceAttribute

        Returns:
            int, float, str: the value of the attribute after attribute expansion

        For attributes with attribute type of 'Attribute Expanded Text' we look for an attribute with same name suffixed with '_attriblist' (this should be ResourceAttribute of the Resource associated with the attribute). If the attriblist attribute is found, we use it to generate a dictionary to use to expand the attribute value, and the expanded value is returned.

        If the expansion fails, or if no attriblist attribute is found, or if the attribute type is not 'Attribute Expanded Text', we just return the raw value.
        """

        raw_value = self.value
        if typed:
            # Try to convert to python type as per AttributeType
            raw_value = self.typed_value()

        if not attribute_expansion.is_expandable_type(self.allocation_attribute_type.attribute_type):
            # We are not an expandable type, return raw_value
            return raw_value

        allocs = [self.allocation] + extra_allocations
        resources = list(self.allocation.resources.all())
        attrib_name = self.allocation_attribute_type.name

        attriblist = attribute_expansion.get_attriblist_str(
            attribute_name=attrib_name, resources=resources, allocations=allocs
        )

        if not attriblist:
            # We do not have an attriblist, return raw_value
            return raw_value

        expanded = attribute_expansion.expand_attribute(
            raw_value=raw_value,
            attribute_name=attrib_name,
            attriblist_string=attriblist,
            resources=resources,
            allocations=allocs,
        )
        return expanded


class AllocationAttributeUsage(TimeStampedModel):
    """Allocation attribute usage indicates the usage of an allocation attribute.

    Attributes:
        allocation_attribute (AllocationAttribute): links the usage to its allocation attribute
        value (float): usage value of the allocation attribute
    """

    allocation_attribute = models.OneToOneField(AllocationAttribute, on_delete=models.CASCADE, primary_key=True)
    value = models.FloatField(default=0)
    history = HistoricalRecords()

    def __str__(self):
        return "{}: {}".format(self.allocation_attribute.allocation_attribute_type.name, self.value)


class AllocationUserStatusChoice(TimeStampedModel):
    """An allocation user status choice indicates the status of an allocation user. Examples include Active, Error, and Removed.

    Attributes:
        name (str): name of the allocation user status choice
    """

    class Meta:
        ordering = [
            "name",
        ]

    class AllocationUserStatusChoiceManager(models.Manager):
        def get_by_natural_key(self, name):
            return self.get(name=name)

    name = models.CharField(max_length=64)
    objects = AllocationUserStatusChoiceManager()

    def __str__(self):
        return self.name

    def natural_key(self):
        return (self.name,)


class AllocationUserRoleChoice(TimeStampedModel):
    """An allocation role choice indicates the role a user has in an allocation.

    Attributes:
        resources (Resource): the resources that have this role
        is_user_default (bool): whether this role is the default for a user with project User status
        is_manager_default (bool): whether this role is the default for a user with project Manager status
    """

    name = models.CharField(max_length=64)
    resources = models.ManyToManyField(Resource, blank=True)
    is_user_default = models.BooleanField(default=False)
    is_manager_default = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    class Meta:
        ordering = [
            "name",
        ]

    def clean(self):
        """Validates the allocation user role defaults and raises errors if they are invalid."""

        if self.is_user_default:
            for role_choice in AllocationUserRoleChoice.objects.all().exclude(pk=self.pk):
                for resource in role_choice.resources.all():
                    if resource in self.resources.all() and role_choice.is_user_default:
                        raise ValidationError(f"role {role_choice.name} is already set as the user default")
        if self.is_manager_default:
            for role_choice in AllocationUserRoleChoice.objects.all().exclude(pk=self.pk):
                for resource in role_choice.resources.all():
                    if resource in self.resources.all() and role_choice.is_manager_default:
                        raise ValidationError(f"role {role_choice.name} is already set as the manager default")


class AllocationUser(TimeStampedModel):
    """An allocation user represents a user on the allocation.

    Attributes:
        allocation (Allocation): links user to its allocation
        user (User): represents the User object of the allocation user
        status (ProjectUserStatus): links the project user status choice to the user
    """

    allocation = models.ForeignKey(Allocation, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    role = models.ForeignKey(AllocationUserRoleChoice, null=True, on_delete=models.CASCADE)
    status = models.ForeignKey(
        AllocationUserStatusChoice, on_delete=models.CASCADE, verbose_name="Allocation User Status"
    )
    history = HistoricalRecords()

    def is_active(self):
        """Helper function returns True if allocation user status == Active and
        allocation status is one of the accepted active states where users
        should be considered active and have actions taken on them (i.e.
        groups added, accounts created in other systems, etc.)"""

        active_allocation_statuses = [
            "Active",
            "Renewal Requested",
        ]

        return self.status.name == "Active" and self.allocation.status.name in active_allocation_statuses

    def __str__(self):
        return "%s" % (self.user)

    class Meta:
        verbose_name_plural = "Allocation User Status"
        unique_together = ("user", "allocation")


class AllocationAccount(TimeStampedModel):
    """An allocation account
    #come back to

    Attributes:
        user (User): represents the User object of the project user
        name (str):
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=64, unique=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = [
            "name",
        ]


class AllocationChangeStatusChoice(TimeStampedModel):
    """An allocation change status choice represents statuses displayed when a user changes their allocation status (for allocations that have their is_changeable attribute set to True). Examples include Expired and Payment Pending.

    Attributes:
        name (str): status name
    """

    name = models.CharField(max_length=64)

    def __str__(self):
        return self.name

    class Meta:
        ordering = [
            "name",
        ]


class AllocationChangeRequest(TimeStampedModel):
    """An allocation change request represents a request from a PI or manager to change their allocation.

    Attributes:
        allocation (Allocation): represents the allocation to change
        status (AllocationStatusChoice): represents the allocation status of the changed allocation
        end_date_extension (int): represents the number of days to extend the allocation's end date
        justification (str): represents input from the user justifying why they want to change the allocation
        notes (str): represents notes for users changing allocations
    """

    allocation = models.ForeignKey(
        Allocation,
        on_delete=models.CASCADE,
    )
    status = models.ForeignKey(AllocationChangeStatusChoice, on_delete=models.CASCADE, verbose_name="Status")
    end_date_extension = models.IntegerField(blank=True, null=True)
    justification = models.TextField()
    notes = models.CharField(max_length=512, blank=True, null=True)
    history = HistoricalRecords()

    @property
    def get_parent_resource(self):
        """
        Returns:
            Resource: the parent resource for the allocation
        """

        return self.allocation.get_parent_resource

    def __str__(self):
        return "%s (%s) Change Request" % (self.get_parent_resource.name, self.allocation.project.pi)

    def get_absolute_url(self):
        return reverse("allocation-change-detail", kwargs={"pk": self.pk})


class AllocationAttributeChangeRequest(TimeStampedModel):
    """An allocation attribute change request represents a request from a PI/ manager to change their allocation attribute.

    Attributes:
        allocation_change_request (AllocationChangeRequest): links the change request from which this attribute change is derived
        allocation_attribute (AllocationAttribute): represents the allocation_attribute to change
        new_value (str): new value of allocation attribute
    """

    allocation_change_request = models.ForeignKey(AllocationChangeRequest, on_delete=models.CASCADE)
    allocation_attribute = models.ForeignKey(AllocationAttribute, on_delete=models.CASCADE)
    new_value = models.CharField(max_length=128)
    old_value = models.CharField(max_length=128)
    history = HistoricalRecords()

    def __str__(self):
        return "%s" % (self.allocation_attribute.allocation_attribute_type.name)


class AllocationAdminAction(TimeStampedModel):
    """An allocation admin action tracks what an admin is doing on the site.

    Attributes:
        user (User): who the admin was
        allocation (Allocation): the alocation the action was done on
        action (str): what the admin did on the site
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    allocation = models.ForeignKey(Allocation, on_delete=models.CASCADE)
    action = models.CharField(max_length=256)


class AllocationInvoice(TimeStampedModel):
    """An allocation invoice that contains the financial info for an allocation

    Attributes:
        allocation (Allocation): allocation this invoice belongs to
        account_number (str): account number provided to bill
        sub_account_number (str): sub account number provided to bill
        status (AllocationStatusChoice): status of the invoice
    """

    allocation = models.ForeignKey(Allocation, on_delete=models.CASCADE)
    account_number = models.CharField(max_length=9)
    sub_account_number = models.CharField(max_length=20, blank=True, null=True)
    status = models.ForeignKey(AllocationStatusChoice, on_delete=models.CASCADE)

    def __str__(self):
        return "{} Invoice".format(self.allocation.get_parent_resource.name)


class AllocationUserRequestStatusChoice(TimeStampedModel):
    """An allocation user request choice indicates the status of an allocation user request.

    Attributes:
        name (str): name of allocation user request status choice
    """

    name = models.CharField(max_length=64)

    def __str__(self):
        return self.name

    class Meta:
        ordering = [
            "name",
        ]


class AllocationUserRequest(TimeStampedModel):
    """An allocation user request represents a request to add/remove a user from an allocation.

    Attributes:
        requestor_user (User): user who made the request
        allocation_user (AllocationUser): allocation user who the request is about
        allocation_user_status (AllocationUserStatusChoice): new status of the User in the allocation
        status (AllocationUserRequestStatusChoice): status of the request
    """

    requestor_user = models.ForeignKey(User, on_delete=models.CASCADE)
    allocation_user = models.ForeignKey(AllocationUser, on_delete=models.CASCADE)
    allocation_user_status = models.ForeignKey(AllocationUserStatusChoice, on_delete=models.CASCADE)
    status = models.ForeignKey(AllocationUserRequestStatusChoice, on_delete=models.CASCADE)
    history = HistoricalRecords()

    def __str__(self):
        return "{} ({})".format(self.allocation_user.user.username, self.allocation_user_status)
