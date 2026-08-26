from django import forms
from django.contrib.auth.models import User

from coldfront.plugins.pi_change_request.models import (
    ProjectPiChangeRequest,
    ProjectPiChangeRequestResourceApprovalSetting,
)


class ProjectPiChangeRequestForm(forms.ModelForm):
    class Meta:
        model = ProjectPiChangeRequest
        fields = ["new_pi", "justification"]

    def __init__(self, *args, **kwargs):
        project_obj = kwargs.pop("project", None)
        super().__init__(*args, **kwargs)

        if project_obj:
            self.fields["new_pi"].queryset = (
                User.objects.filter(
                    projectuser__project=project_obj,
                    projectuser__status__name="Active",
                    projectuser__role__name="Manager",
                )
                .exclude(pk=project_obj.pi_id)
                .distinct()
            )


class ResourcesRequiringApprovalFormset(forms.BaseFormSet):
    def get_form_kwargs(self, index):
        """Extract the per-form disable flag from the list passed via form_kwargs."""
        kwargs = super().get_form_kwargs(index)
        disable_selected = kwargs["disable_selected"][index]
        return {"disable_selected": disable_selected}


class ResourcesRequiringApprovalForm(forms.ModelForm):
    class Meta:
        model = ProjectPiChangeRequestResourceApprovalSetting
        fields = ["requires_approval"]

    def __init__(self, *args, **kwargs):
        disable_selected = kwargs.pop("disable_selected", None)
        super().__init__(*args, **kwargs)
        if disable_selected:
            self.fields["requires_approval"].disabled = True
