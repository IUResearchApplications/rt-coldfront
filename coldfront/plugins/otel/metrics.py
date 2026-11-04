# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""OpenTelemetry metrics for the otel plugin, exported via OTLP like the spans."""

from opentelemetry import metrics
from opentelemetry.metrics import Observation

meter = metrics.get_meter(__name__)


def _observe_db_connections(options):
    from django.db import connections

    open_count = 0
    for alias in connections:
        try:
            if connections[alias].connection is not None:
                open_count += 1
        except Exception:
            pass
    yield Observation(open_count)


db_connections_open = meter.create_observable_gauge(
    "coldfront.db.connections.open",
    callbacks=[_observe_db_connections],
    description="Database connections currently open in this process",
    unit="{connection}",
)

db_connections_created = meter.create_counter(
    "coldfront.db.connections.created",
    description="Database connections opened since process start",
    unit="{connection}",
)

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
