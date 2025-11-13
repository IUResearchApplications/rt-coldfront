from django.urls import path

from coldfront.plugins.slurm.views import get_full_slurm_help, get_slurm_help

urlpatterns = [
    path("full-slurm-help/", get_full_slurm_help, name="full-slurm-help"),
    path("slurm-help/", get_slurm_help, name="slurm-help"),
]
