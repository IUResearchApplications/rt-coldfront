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
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
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
        RequestsInstrumentor().instrument()

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

            def _server_attributes(target, args=None, kwargs=None):
                # SyncStrategy.open runs on the strategy object, whose connection is exposed as `.connection`.
                connection = getattr(target, "connection", target)
                server = getattr(connection, "server", None)
                attributes = {}
                if server is not None:
                    if server.host:
                        attributes["net.peer.name"] = server.host
                    if server.port:
                        attributes["net.peer.port"] = server.port
                return attributes

            def _base_dn_attributes(connection, args, kwargs):
                base_dn = args[0] if args else kwargs.get("search_base")
                return {"ldap.base_dn": base_dn} if base_dn else {}

            def _traced(operation, original, request_attributes=None, result_attributes=None):
                def wrapper(self, *args, **kwargs):
                    attributes = {"operation": operation}
                    start = time.perf_counter()
                    try:
                        with _tracer.start_as_current_span(f"ldap3.{operation}", attributes=attributes) as span:
                            if request_attributes is not None:
                                try:
                                    span.set_attributes(request_attributes(self, args, kwargs))
                                except Exception:
                                    pass
                            try:
                                result = original(self, *args, **kwargs)
                            except Exception:
                                ldap_operations_total.add(1, {**attributes, "status": "error"})
                                raise
                            if result_attributes is not None:
                                try:
                                    span.set_attributes(result_attributes(self, result))
                                except Exception:
                                    pass
                            ldap_operations_total.add(1, {**attributes, "status": "success"})
                            return result
                    finally:
                        ldap_operation_duration_seconds.record(time.perf_counter() - start, attributes)

                return wrapper

            # The search filter is intentionally not recorded; it can contain user identifiers.
            LdapSyncStrategy.open = _traced(
                "connect",
                _original_open,
                request_attributes=_server_attributes,
            )
            LdapConnection.search = _traced(
                "search",
                _original_search,
                request_attributes=_base_dn_attributes,
                result_attributes=lambda connection, result: {"ldap.result.count": len(connection.entries)},
            )
