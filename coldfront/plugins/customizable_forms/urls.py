from django.urls import path
import importlib

from coldfront.core.utils.common import import_from_settings
from coldfront.plugins.customizable_forms.utils import standardize_resource_name
from coldfront.plugins.customizable_forms.views import AllocationResourceSelectionView, DispatchView, GenericView

ADDITIONAL_CUSTOM_FORMS = import_from_settings('ADDITIONAL_CUSTOM_FORMS', [])


urlpatterns = [
    path(
        'project/<int:project_pk>/create/',
        AllocationResourceSelectionView.as_view(),
        name='custom-allocation-create'
    ),
    path(
        'project/<int:project_pk>/create/<int:resource_pk>',
        DispatchView.as_view(),
        name='resource-form-redirector'
    ),
    path(
        'project/<int:project_pk>/create/<int:resource_pk>/<str:resource_name>',
        GenericView.as_view(),
        name='resource-form'
    )
]

def add_additional_forms():
    for additional_form in ADDITIONAL_CUSTOM_FORMS:
        view_module, view_class = additional_form.get('view').rsplit('.', 1)
        view_class = getattr(importlib.import_module(view_module), view_class)

        resource_name = standardize_resource_name(additional_form.get('resource_name'))
        urlpatterns[-1:-1] = [
            path(
                f'project/<int:project_pk>/create/<int:resource_pk>/{resource_name}',
                view_class.as_view(),
                name=f'{resource_name.lower()}-form'
            )
        ]
