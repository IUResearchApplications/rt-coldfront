# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from django.urls import path

from coldfront.plugins.ldap_user_search.views import LDAPUserSearchView

urlpatterns = [
    path("ldap_user_search/", LDAPUserSearchView.as_view(), name="ldap-user-search"),
]
