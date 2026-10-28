# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""OpenTelemetry metrics for the otel plugin, exported via OTLP like the spans."""

from opentelemetry import metrics

meter = metrics.get_meter(__name__)

view_requests_total = meter.create_counter(
    "coldfront.view.requests",
    description="Requests handled by ColdFront views",
    unit="{request}",
)

view_request_duration_seconds = meter.create_histogram(
    "coldfront.view.request.duration",
    description="Time spent inside a ColdFront view",
    unit="s",
)

ldap_operations_total = meter.create_counter(
    "coldfront.ldap.operations",
    description="LDAP operations performed via ldap3",
    unit="{operation}",
)

ldap_operation_duration_seconds = meter.create_histogram(
    "coldfront.ldap.operation.duration",
    description="Duration of LDAP operations via ldap3",
    unit="s",
)
