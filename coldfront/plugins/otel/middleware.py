# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import time

from django.urls import Resolver404, resolve
from opentelemetry import trace

from coldfront.plugins.otel.metrics import view_request_duration_seconds, view_requests_total

tracer = trace.get_tracer("coldfront.views")


class ViewSpanMiddleware:
    """Create an application span around each view so view logic shows up in traces."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            view_name = resolve(request.path_info).view_name
        except Resolver404:
            view_name = "404"
        name = f"{request.method} {view_name}"
        attributes = {"method": request.method, "view_name": view_name}

        start = time.perf_counter()
        try:
            with tracer.start_as_current_span(name):
                response = self.get_response(request)
        except Exception:
            view_requests_total.add(1, {**attributes, "status_code": "500"})
            raise
        finally:
            view_request_duration_seconds.record(time.perf_counter() - start, attributes)
        view_requests_total.add(1, {**attributes, "status_code": str(response.status_code)})
        return response
