from django import forms


class HistoryReportForm(forms.Form):
    MODEL_CHOICES = (("allocation","Allocation"), ("project", "Project"))

    model = forms.ChoiceField(choices=MODEL_CHOICES, required=True)
    id = forms.IntegerField(required=True)
