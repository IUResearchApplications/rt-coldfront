# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later


from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import MinLengthValidator
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404

from coldfront.core.project.models import (
    Project,
    ProjectAttribute,
    ProjectReview,
    ProjectUserRoleChoice,
)
from coldfront.core.project.utils import check_if_pi_eligible
from coldfront.core.utils.common import get_user_info, import_from_settings

if "coldfront.plugins.ldap_misc" in settings.INSTALLED_APPS:
    from coldfront.plugins.ldap_misc.utils.ldap_user_search import get_user_info
    from coldfront.plugins.ldap_misc.utils.project import check_if_pi_eligible

EMAIL_DIRECTOR_PENDING_PROJECT_REVIEW_EMAIL = import_from_settings("EMAIL_DIRECTOR_PENDING_PROJECT_REVIEW_EMAIL")
EMAIL_ADMIN_LIST = import_from_settings("EMAIL_ADMIN_LIST", [])
EMAIL_DIRECTOR_EMAIL_ADDRESS = import_from_settings("EMAIL_DIRECTOR_EMAIL_ADDRESS", "")

ADDITIONAL_USER_SEARCH_CLASSES = import_from_settings("ADDITIONAL_USER_SEARCH_CLASSES", [])


class ProjectSearchForm(forms.Form):
    """Search form for the Project list page."""

    LAST_NAME = "Last Name"
    USERNAME = "Username"
    # FIELD_OF_SCIENCE = "Field of Science"

    last_name = forms.CharField(label=LAST_NAME, max_length=100, required=False)
    username = forms.CharField(label=USERNAME, max_length=100, required=False)
    # field_of_science = forms.CharField(label=FIELD_OF_SCIENCE, max_length=100, required=False)
    show_all_projects = forms.BooleanField(initial=False, required=False)


class ProjectAddUserForm(forms.Form):
    username = forms.CharField(max_length=150, disabled=True)
    first_name = forms.CharField(max_length=150, required=False, disabled=True)
    last_name = forms.CharField(max_length=150, required=False, disabled=True)
    email = forms.EmailField(max_length=100, required=False, disabled=True)
    source = forms.CharField(max_length=16, disabled=True)
    role = forms.ModelChoiceField(queryset=ProjectUserRoleChoice.objects.all(), empty_label=None)
    selected = forms.BooleanField(initial=False, required=False)


class ProjectAddUsersToAllocationFormSet(forms.BaseFormSet):
    def get_form_kwargs(self, index):
        """
        Override so allocations can have role selection
        """
        kwargs = super().get_form_kwargs(index)
        roles = kwargs["roles"][index]
        return {"roles": roles}


class ProjectAddUsersToAllocationForm(forms.Form):
    pk = forms.IntegerField(disabled=True)
    selected = forms.BooleanField(initial=False, required=False)
    resource = forms.CharField(max_length=50, disabled=True)
    details = forms.CharField(max_length=300, disabled=True, required=False)
    resource_type = forms.CharField(max_length=50, disabled=True)
    status = forms.CharField(max_length=50, disabled=True)
    role = forms.ChoiceField(choices=(("", "----"),), disabled=True, required=False)

    def __init__(self, *args, **kwargs):
        roles = kwargs.pop("roles")
        super().__init__(*args, **kwargs)
        if roles:
            self.fields["role"].disabled = False
            self.fields["role"].choices = tuple([(role, role) for role in roles])


class ProjectRemoveUserForm(forms.Form):
    username = forms.CharField(max_length=150, disabled=True)
    first_name = forms.CharField(max_length=150, required=False, disabled=True)
    last_name = forms.CharField(max_length=150, required=False, disabled=True)
    email = forms.EmailField(max_length=100, required=False, disabled=True)
    role = forms.CharField(max_length=30, disabled=True)
    selected = forms.BooleanField(initial=False, required=False)


class ProjectUserUpdateForm(forms.Form):
    role = forms.ModelChoiceField(queryset=ProjectUserRoleChoice.objects.all(), empty_label=None)
    enable_notifications = forms.BooleanField(initial=False, required=False)


class ProjectReviewForm(forms.Form):
    no_project_updates = forms.BooleanField(label="No new project updates", required=False)
    project_updates = forms.CharField(label="Project updates", widget=forms.Textarea(), required=False)
    acknowledgement = forms.BooleanField(
        label="By checking this box I acknowledge that I have updated my project to the best of my knowledge",
        initial=False,
        required=True,
    )

    def clean(self):
        cleaned_data = super().clean()
        project_updates = cleaned_data.get("project_updates")
        no_project_updates = cleaned_data.get("no_project_updates")
        if not no_project_updates and project_updates == "":
            raise forms.ValidationError("Please fill out the project updates field.")


class ProjectReviewEmailForm(forms.Form):
    cc = forms.CharField(required=False)
    email_body = forms.CharField(required=True, widget=forms.Textarea)

    def __init__(self, pk, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        project_review_obj = get_object_or_404(ProjectReview, pk=int(pk))
        self.fields["email_body"].initial = EMAIL_DIRECTOR_PENDING_PROJECT_REVIEW_EMAIL.format(
            first_name=user.first_name, project_name=project_review_obj.project.title
        )
        cc_list = [project_review_obj.project.pi.email, user.email]
        if project_review_obj.project.pi == project_review_obj.project.requestor:
            cc_list.remove(project_review_obj.project.pi.email)
        self.fields["cc"].initial = ", ".join(cc_list)


class ProjectAttributeAddForm(forms.ModelForm):
    class Meta:
        fields = "__all__"
        model = ProjectAttribute
        labels = {
            "proj_attr_type": "Project Attribute Type",
        }

    def __init__(self, *args, **kwargs):
        super(ProjectAttributeAddForm, self).__init__(*args, **kwargs)
        user = (kwargs.get("initial")).get("user")
        self.fields["proj_attr_type"].queryset = self.fields["proj_attr_type"].queryset.order_by(Lower("name"))
        if not user.is_superuser and not user.has_perm("project.delete_projectattribute"):
            self.fields["proj_attr_type"].queryset = self.fields["proj_attr_type"].queryset.filter(is_private=False)


class ProjectAttributeDeleteForm(forms.Form):
    pk = forms.IntegerField(required=False, disabled=True)
    name = forms.CharField(max_length=150, required=False, disabled=True)
    attr_type = forms.CharField(max_length=150, required=False, disabled=True)
    value = forms.CharField(max_length=150, required=False, disabled=True)
    selected = forms.BooleanField(initial=False, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["pk"].widget = forms.HiddenInput()


# class ProjectAttributeChangeForm(forms.Form):
#     pk = forms.IntegerField(required=False, disabled=True)
#     name = forms.CharField(max_length=150, required=False, disabled=True)
#     value = forms.CharField(max_length=150, required=False, disabled=True)
#     new_value = forms.CharField(max_length=150, required=False, disabled=False)

#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)
#         self.fields['pk'].widget = forms.HiddenInput()

#     def clean(self):
#         cleaned_data = super().clean()

#         if cleaned_data.get('new_value') != "":
#             proj_attr = ProjectAttribute.objects.get(pk=cleaned_data.get('pk'))
#             proj_attr.value = cleaned_data.get('new_value')
#             proj_attr.clean()


class ProjectAttributeUpdateForm(forms.Form):
    pk = forms.IntegerField(required=False, disabled=True)
    new_value = forms.CharField(max_length=150, required=True, disabled=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["pk"].widget = forms.HiddenInput()

    def clean(self):
        cleaned_data = super().clean()

        if cleaned_data.get("new_value") != "":
            proj_attr = ProjectAttribute.objects.get(pk=cleaned_data.get("pk"))
            proj_attr.value = cleaned_data.get("new_value")
            proj_attr.clean()


class ProjectCreationForm(forms.ModelForm):
    pi_username = forms.CharField(
        max_length=20,
        label="PI Username",
        required=False,
        help_text=(
            "Required if you will not be the PI of this project. Only faculty and staff can be the PI. "
            "They must log onto the site at least once before they can be added."
        ),
    )
    class_number = forms.CharField(max_length=25, required=False)

    class Meta:
        model = Project
        fields = ["title", "description", "pi_username", "type", "class_number", "requestor", "pi"]

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["pi_username"].required = not check_if_pi_eligible(user.username)
        self.fields["description"].widget.attrs.update(
            {
                "placeholder": (
                    "EXAMPLE: Our research involves the collection, storage, and analysis of rat "
                    "colony behaviorial footage to study rat social patterns in natural settings. "
                    "We intend to store the footage in a shared Slate-Project directory, perform "
                    "cleaning of the footage with the Python library Pillow, and then perform "
                    "video classification analysis on the footage using Python libraries such as "
                    "TorchVision using Quartz and Big Red 200."
                )
            }
        )
        self.fields["requestor"].initial = user
        self.fields["requestor"].widget = forms.HiddenInput()
        self.fields["pi"].initial = user
        self.fields["pi"].widget = forms.HiddenInput()

    def clean(self):
        cleaned_data = super().clean()
        requestor = cleaned_data.get("requestor")
        pi_username = cleaned_data.get("pi_username")
        if pi_username:
            pi_obj = User.objects.filter(username=pi_username).first()
        else:
            pi_obj = requestor
        if pi_obj is None:
            user_info = get_user_info(pi_username)
            if user_info is not None and not user_info:
                raise forms.ValidationError({"pi_username": "This PI's username does not exist."})

            raise forms.ValidationError(
                {
                    "pi_username": (
                        "This PI's username could not be found on RT Projects. "
                        "They need to log onto this site for their account to be "
                        "automatically created. Afterwards, they can be added as a PI to this "
                        "project."
                    )
                }
            )

        if not check_if_pi_eligible(pi_obj.username):
            if pi_username:
                message = {"pi_username": "Only faculty and staff can be the PI"}
            else:
                message = "Only faculty and staff can be the PI"
            raise forms.ValidationError(message)

        cleaned_data["pi"] = pi_obj
        return cleaned_data


class ProjectRequestEmailForm(forms.Form):
    cc = forms.CharField(required=False)
    email_body = forms.CharField(required=True, widget=forms.Textarea)

    def __init__(self, pk, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        project_obj = get_object_or_404(Project, pk=int(pk))
        self.fields["email_body"].initial = EMAIL_DIRECTOR_PENDING_PROJECT_REVIEW_EMAIL.format(
            first_name=user.first_name, project_name=project_obj.title
        )
        cc_list = [project_obj.pi.email, user.email]
        if project_obj.pi == project_obj.requestor:
            cc_list.remove(project_obj.pi.email)
        self.fields["cc"].initial = ", ".join(cc_list)


class ProjectReviewAllocationForm(forms.Form):
    pk = forms.IntegerField(disabled=True)
    resource = forms.CharField(max_length=100, disabled=True)
    users = forms.CharField(max_length=2000, disabled=True, required=False)
    status = forms.CharField(max_length=50, disabled=True)
    expires_on = forms.DateField(widget=forms.DateInput(attrs={"class": "datepicker"}), disabled=True)
    renew = forms.BooleanField(initial=True, required=False)


class ProjectUpdateForm(forms.Form):
    title = forms.CharField(
        max_length=255,
    )
    description = forms.CharField(
        validators=[
            MinLengthValidator(
                10,
                "The project description must be > 10 characters",
            )
        ],
        widget=forms.Textarea,
    )

    def __init__(self, project_pk, *args, **kwargs):
        super().__init__(*args, **kwargs)
        project_obj = get_object_or_404(Project, pk=project_pk)

        self.fields["title"].initial = project_obj.title
        self.fields["description"].initial = project_obj.description
