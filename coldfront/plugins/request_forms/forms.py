from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit
from django import forms


class SoftwareRequestForm(forms.Form):
    DESTINATION_CHOICES = (("Big Red 200", "Big Red 200"), ("Quartz", "Quartz"))

    software_name = forms.CharField(max_length=50, label="Software Name:")
    destination = forms.ChoiceField(choices=DESTINATION_CHOICES, label="Destination:", widget=forms.RadioSelect)
    software_version = forms.CharField(
        max_length=50, label="Software Version:", help_text="Software version/package number"
    )
    vendor_url = forms.URLField(max_length=100, label="Vendor URL:", help_text="Vendor URL for source code/binaries")
    confirmed_users = forms.IntegerField(
        min_value=1,
        label="Confirmed Users:",
        initial=1,
        help_text="Number of confirmed users of the requested software per system",
    )
    use_case = forms.CharField(
        widget=forms.Textarea,
        label="Use Case:",
        help_text="Please describe why this software is needed",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.add_input(Submit("submit", "Submit"))

        self.fields["vendor_url"].widget.attrs.update({"placeholder": "https://"})


class StatsRequestForm(forms.Form):
    description = forms.CharField(label="What data you are looking for?", widget=forms.Textarea)
    question_addressed = forms.CharField(label="What question are you trying to address?", widget=forms.Textarea)
    deadline = forms.DateField(
        label="By what date do you need this information?",
        widget=forms.DateInput(attrs={"class": "datepicker"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.add_input(Submit("submit", "Submit"))
