# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Create your views here.

from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render
from django.views.generic import View

from coldfront.plugins.ldap_user_search.utils import get_user_info


class LDAPUserSearchView(LoginRequiredMixin, View):
    def post(self, request):
        context = {
            "username_exists": False,
            "name": None,
            "email": None,
            "id": request.POST.get("id"),
            "message": "Invalid username",
        }

        attributes = get_user_info(request.POST.get("username"))
        display_name = attributes.get("displayName")
        # If one exists so does the other
        if display_name:
            context["username_exists"] = True
            context["name"] = display_name
            context["email"] = attributes.get("email")
            context["message"] = "Valid username"

        return render(request, "username_search_result.html", context)
