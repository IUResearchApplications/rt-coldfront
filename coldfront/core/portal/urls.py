# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from django.urls import path

import coldfront.core.portal.views as portal_views

urlpatterns = [
    path(
        "data/allocation-by-status/",
        portal_views.allocation_by_status,
        name="portal-allocation-status",
    ),
    path(
        "data/resource-by-type/",
        portal_views.resource_by_type,
        name="portal-resource-type",
    ),
    path(
        "data/project-by-type/",
        portal_views.project_by_type,
        name="portal-project-type",
    ),
    path(
        "data/project-type-by-user-count/",
        portal_views.project_type_by_user_count,
        name="portal-project-user",
    ),
    path(
        "data/users-by-year/",
        portal_views.users_by_year,
        name="portal-users-by-year",
    ),
    path(
        "data/users-by-active/",
        portal_views.users_by_active,
        name="portal-users-by-active",
    ),
]
