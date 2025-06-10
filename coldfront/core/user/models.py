import datetime
from ast import literal_eval
from django.contrib.auth.models import User
from django.db import models
from django.core.exceptions import ValidationError
from model_utils.models import TimeStampedModel
from simple_history.models import HistoricalRecords

from coldfront.core import attribute_expansion


class UserProfile(models.Model):
    """ Displays a user's profile. A user can be a principal investigator (PI), manager, administrator, staff member, billing staff member, or center director.

    Attributes:
        is_pi (bool): indicates whether or not the user is a PI
        user (User): represents the Django User model
        department (str): the department the user is in
        division (str): the department code
        title (str): the user's status
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    is_pi = models.BooleanField(default=False)
    department = models.CharField(max_length=100, default='')
    division = models.CharField(max_length=100, default='')
    title = models.CharField(max_length=30, default='')


class AttributeType(TimeStampedModel):
    """ An attribute type indicates the data type of the attribute. Examples include Date, Float, Int, Text, and Yes/No. 
    
    Attributes:
        name (str): name of attribute data type
    """

    name = models.CharField(max_length=64)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name', ]


class UserProfileAttributeType(TimeStampedModel):
    """ A user profile attribute type indicates the type of the attribute. 

    Attributes:
        attribute_type (AttributeType): indicates the data type of the attribute
        name (str): name of allocation attribute type
        is_required (bool): indicates whether or not the attribute is required
        is_unique (bool): indicates whether or not the value is unique
        is_private (bool): indicates whether or not the attribute type is private
    """

    attribute_type = models.ForeignKey(AttributeType, on_delete=models.CASCADE)
    name = models.CharField(max_length=50)
    is_required = models.BooleanField(default=False)
    is_unique = models.BooleanField(default=False)
    is_private = models.BooleanField(default=True)
    history = HistoricalRecords()

    def __str__(self):
        return '%s (%s)' % (self.name, self.attribute_type.name)

    def get_linked_resources(self):
        return self.linked_resources.all()

    class Meta:
        ordering = ['name', ]


class UserProfileAttribute(TimeStampedModel):
    """ A user profile attribute class links an user profile attribute type and a user profile. 

    Attributes:
        user_profile_attribute_type (UserProfileAttributeType): attribute type to link
        user_profile (UserProfile): allocation to link
        value (str): value of the allocation attribute
    """

    user_profile_attribute_type = models.ForeignKey(
        UserProfileAttributeType, on_delete=models.CASCADE)
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE)
    value = models.CharField(max_length=128, db_collation='utf8mb4_0900_ai_ci')
    history = HistoricalRecords()

    def clean(self):
        """ Validates the user profile attribute and raises errors if the user profile attribute is invalid. """

        if self.user_profile_attribute_type.is_unique and self.allocation.allocationattribute_set.filter(allocation_attribute_type=self.allocation_attribute_type).exclude(id=self.pk).exists():
            raise ValidationError("'{}' attribute already exists for this user profile.".format(
                self.user_profile_attribute_type))

        expected_value_type = self.user_profile_attribute_type.attribute_type.name.strip()

        if expected_value_type == "Int" and not isinstance(literal_eval(self.value), int):
            raise ValidationError(
                'Invalid Value "%s" for "%s". Value must be an integer.' % (self.value, self.user_profile_attribute_type.name))
        elif expected_value_type == "Float" and not (isinstance(literal_eval(self.value), float) or isinstance(literal_eval(self.value), int)):
            raise ValidationError(
                'Invalid Value "%s" for "%s". Value must be a float.' % (self.value, self.user_profile_attribute_type.name))
        elif expected_value_type == "Yes/No" and self.value not in ["Yes", "No"]:
            raise ValidationError(
                'Invalid Value "%s" for "%s". Allowed inputs are "Yes" or "No".' % (self.value, self.user_profile_attribute_type.name))
        elif expected_value_type == "Date":
            try:
                datetime.datetime.strptime(self.value.strip(), "%Y-%m-%d")
            except ValueError:
                raise ValidationError(
                    'Invalid Value "%s" for "%s". Date must be in format YYYY-MM-DD' % (self.value, self.user_profile_attribute_type.name))

    def __str__(self):
        return '%s' % (self.user_profile_attribute_type.name)

    def typed_value(self):
        """
        Returns:
            int, float, str: the value of the attribute with proper type and is used for computing expanded_value() (coerced into int or float for attributes with Int or Float types; if it fails or the attribute is of any other type, it is coerced into a str)
        """

        raw_value = self.value
        atype_name = self.user_profile_attribute_type.attribute_type.name
        return attribute_expansion.convert_type(
            value=raw_value, type_name=atype_name)
