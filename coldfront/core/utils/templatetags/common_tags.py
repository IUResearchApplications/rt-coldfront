# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later
import json

from django import template
from django.conf import settings
from django.utils.safestring import mark_safe

register = template.Library()


# settings value
@register.simple_tag
def settings_value(name):
    allowed_names = [
        "LOGIN_FAIL_MESSAGE",
        "ACCOUNT_CREATION_TEXT",
        "CENTER_NAME",
        "CENTER_HELP_URL",
        "EMAIL_PROJECT_REVIEW_CONTACT",
        "EMAIL_TICKET_SYSTEM_ADDRESS",
    ]
    # FIXME: This is using mark_safe for now but settings should not contain HTML in the future
    return mark_safe(getattr(settings, name, "") if name in allowed_names else "")  # noqa: S308


@register.filter
def get_icon(expand_accordion):
    if expand_accordion == "show":
        return "fa-minus"
    else:
        return "fa-plus"


@register.filter
def convert_boolean_to_icon(boolean):
    if boolean is False:
        return mark_safe('<span class="badge bg-success"><i class="fas fa-check"></i></span>')
    else:
        return mark_safe('<span class="badge bg-danger"><i class="fas fa-times"></i></span>')


@register.filter
def convert_status_to_icon(project):
    last_project_review = project.last_project_review
    needs_review = project.needs_review
    if last_project_review:
        status = last_project_review.status.name
        if status == "Pending":
            return mark_safe('<h4><span class="badge bg-info"><i class="fas fa-exclamation-circle"></i></span></h4>')
        elif status == "Completed":
            return mark_safe('<h4><span class="badge bg-success"><i class="fas fa-check-circle"></i></span></h4>')
    elif needs_review and not last_project_review:
        return mark_safe('<h4><span class="badge bg-danger"><i class="fas fa-question-circle"></i></span></h4>')
    elif not needs_review:
        return mark_safe('<h4><span class="badge bg-success"><i class="fas fa-check-circle"></i></span></h4>')


@register.filter()
def color_text(status):
    if status in [
        "Active",
    ]:
        return "text-success"

    if status in [
        "Expired",
        "Denied",
        "Renewal Denied",
        "Removed",
        "Revoked",
    ]:
        return "text-danger"

    return "text-primary"


@register.filter("get_value_from_dict")
def get_value_from_dict(dict_data, key):
    """
    usage example {{ your_dict|get_value_from_dict:your_key }}
    """
    if key and dict_data:
        if type(dict_data) is str:
            dict_data = json.loads(dict_data)
        return dict_data.get(key)


@register.filter("get_value_by_index")
def get_value_by_index(array, index):
    """
    usage example {{ your_list|get_value_by_index:your_index }}
    """
    return array[index]


@register.simple_tag
def navbar_active_item(menu_item, request):
    view_map = {
        "center-summary": ["center-summary"],
        "home": ["home", "request_forms:software-request", "request_forms:stats-request"],
        "invoice": [
            "allocation-invoice-list",
            "allocation-invoice-detail",
            "allocation-add-invoice-note",
            "allocation-update-invoice-note",
            "allocation-delete-invoice-note",
        ],
        "project": [
            "project-list",
            "project-detail",
            "project-archive",
            "project-archived-list",
            "project-create",
            "project-update",
            "project-add-users-search",
            "project-remove-users",
            "project-user-detail",
            "project-review",
            "project-note-add",
            "project-attribute-create",
            "project-attribute-delete",
            "project-attribute-update",
            "project-denied-list",
            "allocation-detail",
            "allocation-list",
            "allocation-account-list",
            "allocation-create",
            "allocation-change-detail",
            "allocation-add-users",
            "allocation-remove-users",
            "allocation-renew",
            "allocation-attribute-add",
            "allocation-change",
            "allocation-attribute-edit",
            "allocation-attribute-delete",
            "allocation-note-add",
            "allocation-note-update",
            "allocation-user-detail",
            "allocation-review-eula",
            "resource-list",
            "user-list-allocations",
            "publication-search",
            "add-publication-manually",
            "publication-delete-publications",
            "publication-export-publications",
            "allocation_removal_requests:allocation-removal-request",
            "move-allocation",
            "custom-allocation-create",
        ],
        "admin": [
            "user-search-home",
            "project-review-list",
            "allocation-request-list",
            "allocation-change-list",
            "grant-report",
            "advanced-search",
            "project-review-info",
            "allocation_removal_requests:allocation-removal-request-list",
        ],
        "staff": [
            "user-search-home",
            "project-review-list",
            "allocation-request-list",
            "grant-report",
            "project-review-info",
            "allocation_removal_requests:allocation-removal-request-list",
        ],
        "director": ["project-review-list", "grant-report"],
        "publications": ["publication_catalogue", "publication_gallery"],
        "help": ["get-help"],
        "user": ["user-profile", "user-projects-managers"],
    }
    if request != "" and request.resolver_match is not None:
        view_name = request.resolver_match.view_name
        if menu_item in view_map:
            if view_name in view_map[menu_item]:
                return "active"
    return ""


@register.filter
def split(string, char):
    return string.split(char)


@register.filter
def change_sign(int):
    return -int


@register.filter
def divide(int, divisor):
    return int // divisor


@register.filter
def template_exists(value):
    try:
        template.loader.get_template(value)
        return True
    except template.TemplateDoesNotExist:
        return False
