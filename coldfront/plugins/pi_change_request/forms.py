from django import forms
from django.contrib.auth.models import User

from coldfront.plugins.pi_change_request.models import ProjectPiChangeRequest


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
