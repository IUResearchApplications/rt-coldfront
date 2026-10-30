# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import importlib.util
import time

from django.apps import AppConfig


class OtelConfig(AppConfig):
    name = "coldfront.plugins.otel"

    def ready(self):
        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.dbapi import trace_integration
        from opentelemetry.instrumentation.django import DjangoInstrumentor
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        from coldfront.plugins.otel.sanitization import redact_request_hook

        LoggingInstrumentor().instrument(inject_trace_context=True, enable_log_auto_instrumentation=False)

        resource = Resource.create()
        trace.set_tracer_provider(TracerProvider(resource=resource))
        trace.get_tracer_provider().add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        metrics.set_meter_provider(
            MeterProvider(resource=resource, metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())])
        )

        DjangoInstrumentor().instrument(request_hook=redact_request_hook)
        RedisInstrumentor().instrument()

        if importlib.util.find_spec("MySQLdb") is not None:
            import MySQLdb

            trace_integration(
                MySQLdb,
                "connect",
                "mysql",
                connection_attributes={"database": "db", "host": "host", "user": "user", "port": "port"},
            )

        try:
            from ldap3.core.connection import Connection as LdapConnection
            from ldap3.core.connection import SyncStrategy as LdapSyncStrategy
        except ImportError:
            LdapConnection = None

        if (
            LdapConnection is not None
            and LdapConnection.search.__name__ == "search"
            and LdapSyncStrategy.open.__name__ == "open"
        ):
            from coldfront.plugins.otel.metrics import (
                ldap_operation_duration_seconds,
                ldap_operations_total,
            )

            _tracer = trace.get_tracer(__name__)
            _original_open = LdapSyncStrategy.open
            _original_search = LdapConnection.search

            def _traced(operation, original):
                def wrapper(self, *args, **kwargs):
                    attributes = {"operation": operation}
                    start = time.perf_counter()
                    try:
                        with _tracer.start_as_current_span(f"ldap3.{operation}"):
                            return original(self, *args, **kwargs)
                    except Exception:
                        ldap_operations_total.add(1, {**attributes, "status": "error"})
                        raise
                    finally:
                        ldap_operation_duration_seconds.record(time.perf_counter() - start, attributes)
                    ldap_operations_total.add(1, {**attributes, "status": "success"})

                return wrapper

            LdapSyncStrategy.open = _traced("connect", _original_open)
            LdapConnection.search = _traced("search", _original_search)
