from django.urls import path
import importlib

from coldfront.core.utils.common import import_from_settings
from coldfront.plugins.customizable_forms.utils import standardize_resource_name
from coldfront.plugins.customizable_forms.views import AllocationResourceSelectionView, DispatchView, GenericView

ADDITIONAL_CUSTOM_FORMS = import_from_settings('ADDITIONAL_CUSTOM_FORMS', [])
ADDITIONAL_CUSTOM_GENERIC_FORM = import_from_settings('ADDITIONAL_CUSTOM_GENERIC_FORM', '')


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
    for resource_name, values in ADDITIONAL_CUSTOM_FORMS.items():
        view_module, view_class = values.get('view').rsplit('.', 1)
        view_class = getattr(importlib.import_module(view_module), view_class)

        resource_name = standardize_resource_name(resource_name)
        urlpatterns[-1:-1] = [
            path(
                f'project/<int:project_pk>/create/<int:resource_pk>/{resource_name}',
                view_class.as_view(),
                name=f'{resource_name.lower()}-form'
            )
        ]


def replace_generic_form():
    if ADDITIONAL_CUSTOM_GENERIC_FORM:
        view_module, view_class = ADDITIONAL_CUSTOM_GENERIC_FORM.rsplit('.', 1)
        view_class = getattr(importlib.import_module(view_module), view_class)
        urlpatterns.pop()
        urlpatterns.append(
            path(
                'project/<int:project_pk>/create/<int:resource_pk>/<str:resource_name>',
                view_class.as_view(),
                name='resource-form'
            )
        )
