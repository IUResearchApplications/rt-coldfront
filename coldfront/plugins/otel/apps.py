# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import importlib.util
import time

from django.apps import AppConfig


class OtelConfig(AppConfig):
    name = "coldfront.plugins.otel"

    def ready(self):
        # Imports live inside each step rather than at module level: importing
        # otel instrumentors while Django is still loading apps can raise
        # AppRegistryNotReady.
        self._setup_providers()
        self._instrument_web()
        self._instrument_mysql()
        self._instrument_ldap()
        self._instrument_django_q()
        self._instrument_db_connections()

    def _setup_providers(self):
        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        from coldfront.plugins.otel.filters import DropStatementSpanProcessor

        # Run before set_tracer_provider so log records already carry the
        # otelTraceID/otelSpanID fields used by the log formatter.
        LoggingInstrumentor().instrument(inject_trace_context=True, enable_log_auto_instrumentation=False)

        resource = Resource.create()
        trace.set_tracer_provider(TracerProvider(resource=resource))
        trace.get_tracer_provider().add_span_processor(
            # django-q polls its schedule table every second, and MySQL replays
            # its connection handshake (isolation level + version/feature
            # detection) on every new connection; all noise.
            DropStatementSpanProcessor(
                BatchSpanProcessor(OTLPSpanExporter()),
                (
                    "django_q_",
                    "SET SESSION TRANSACTION ISOLATION LEVEL",
                    "SELECT VERSION(), @@sql_mode",
                ),
            )
        )
        metrics.set_meter_provider(
            MeterProvider(resource=resource, metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())])
        )

    def _instrument_web(self):
        from opentelemetry.instrumentation.django import DjangoInstrumentor
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        from coldfront.plugins.otel.sanitization import redact_request_hook

        DjangoInstrumentor().instrument(request_hook=redact_request_hook)
        RequestsInstrumentor().instrument()

    def _instrument_mysql(self):
        if importlib.util.find_spec("MySQLdb") is None:
            return

        import MySQLdb
        from opentelemetry.instrumentation.dbapi import trace_integration

        trace_integration(
            MySQLdb,
            "connect",
            "mysql",
            connection_attributes={"database": "db", "host": "host", "user": "user", "port": "port"},
        )

    def _instrument_ldap(self):
        from opentelemetry import trace

        from coldfront.plugins.otel.metrics import ldap_operation_duration_seconds, ldap_operations_total

        try:
            from ldap3.core.connection import Connection as LdapConnection
            from ldap3.core.connection import SyncStrategy as LdapSyncStrategy
        except ImportError:
            return

        if LdapConnection.search.__name__ != "search" or LdapSyncStrategy.open.__name__ != "open":
            return

        tracer = trace.get_tracer(__name__)
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
                    with tracer.start_as_current_span(f"ldap3.{operation}", attributes=attributes) as span:
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

    def _instrument_django_q(self):
        from django_q.signals import post_execute_in_worker, pre_execute
        from opentelemetry import context as otel_context
        from opentelemetry import trace

        job_tracer = trace.get_tracer(__name__)
        active_jobs = {}

        def _on_pre_execute(sender, func, task, **kwargs):
            func_name = task.get("func")
            if callable(func_name):
                func_name = getattr(func_name, "__qualname__", None) or getattr(func_name, "__name__", str(func_name))
            span = job_tracer.start_span(
                f"djangoq.execute {func_name}",
                attributes={
                    "djangoq.task_id": task.get("id") or "",
                    "djangoq.task_name": task.get("name") or "",
                },
            )
            context_token = otel_context.attach(trace.set_span_in_context(span))
            active_jobs[id(task)] = (span, context_token)

        def _on_post_execute(sender, task, **kwargs):
            job = active_jobs.pop(id(task), None)
            if job is None:
                return
            span, context_token = job
            otel_context.detach(context_token)
            span.end()

        # weak=False: the receivers are closures that would otherwise be garbage-collected
        # once ready() returns.
        pre_execute.connect(_on_pre_execute, dispatch_uid="otel.djangoq.pre_execute", weak=False)
        post_execute_in_worker.connect(_on_post_execute, dispatch_uid="otel.djangoq.post_execute", weak=False)

    def _instrument_db_connections(self):
        from django.db.backends.signals import connection_created

        from coldfront.plugins.otel.metrics import db_connections_created

        def _on_connection_created(sender, connection, **kwargs):
            db_connections_created.add(1, {"db.alias": connection.alias})

        connection_created.connect(_on_connection_created, dispatch_uid="otel.db.connection_created", weak=False)
