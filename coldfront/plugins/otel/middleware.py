# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from django.urls import Resolver404, resolve
from opentelemetry import trace

tracer = trace.get_tracer("coldfront.views")


class ViewSpanMiddleware:
    """Create an application span around each view so view logic shows up in traces."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            name = f"{request.method} {resolve(request.path_info).view_name}"
        except Resolver404:
            name = f"{request.method} {request.path_info}"
        with tracer.start_as_current_span(name):
            return self.get_response(request)
